"""Capture one official bulletin and prepare a separately reviewed outcome."""

import hashlib
import json
import math
import os
import re
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta, timezone
from html.parser import HTMLParser
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from src.ledger import MAX_RECORD_BYTES, SERIES, UNITS, validate_issuance
from src.provenance import digest

MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_JSON_BYTES = 128 * 1024
MAX_ELEMENTS = 30000
MAX_DEPTH = 64
TIMEOUT_SECONDS = 15
MAX_REDIRECTS = 2
MAX_METADATA_LEADS = 32
MAX_METADATA_TEXT = 2048
REPRESENTATION = "HTTP entity body bytes after transfer framing, no decompression"
LIMITATION = (
    "This checks declared consistency, not the truth of a manual declaration or "
    "the authenticity of supplied ledger history. No observation was appended."
)


def _time(value):
    try:
        result = datetime.fromisoformat(value)
        if result.utcoffset() is None:
            raise ValueError("missing timezone")
        return result
    except (TypeError, ValueError) as error:
        raise ValueError("timestamps require an explicit timezone offset") from error


def _clock(clock):
    value = clock()
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("capture clock must be an aware datetime")
    return value.astimezone(UTC)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)


def _text(value, maximum=600):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def release_url(url):
    """Accept only the declared monthly Freight newsroom route on www.bts.gov."""
    if (
        not isinstance(url, str)
        or len(url) > 2048
        or any(ord(value) <= 32 or ord(value) >= 127 for value in url)
    ):
        raise ValueError("unsupported official bulletin URL")
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname == "www.bts.gov"
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError as error:
        raise ValueError("unsupported official bulletin URL") from error
    match = re.fullmatch(
        r"/newsroom/("
        + "|".join(value.lower() for value in MONTHS)
        + r")-(20\d{2})-freight-transportation-services-index-tsi-[a-z0-9-]{1,240}",
        parsed.path,
    )
    if not valid or match is None:
        raise ValueError("unsupported official bulletin URL")
    month = MONTHS.index(match[1].capitalize()) + 1
    return f"{match[2]}-{month:02}", parsed.path


class _Redirects(HTTPRedirectHandler):
    def __init__(self, url):
        super().__init__()
        self.path = release_url(url)[1]
        self.followed = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            allowed = release_url(newurl)[1] == self.path
        except ValueError:
            allowed = False
        if not allowed or len(self.followed) >= MAX_REDIRECTS:
            raise HTTPError(
                req.full_url, code, "unsupported or excessive BTS redirect", headers, fp
            )
        self.followed.append({"status": code, "url": newurl})
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _Element:
    def __init__(self, tag, attrs, line, parent):
        self.tag, self.attrs, self.line, self.parent = tag, dict(attrs), line, parent
        self.children = []

    def text(self):
        if self.tag in ("script", "style"):
            return ""
        return " ".join(
            child.text() if isinstance(child, _Element) else child
            for child in self.children
        ).strip()


class _Document(HTMLParser):
    """Keep a bounded source tree without executing scripts or loading assets."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Element("root", {}, 1, None)
        self.stack, self.elements = [self.root], []

    def handle_starttag(self, tag, attrs):
        if (
            len(self.elements) >= MAX_ELEMENTS
            or len(self.stack) >= MAX_DEPTH
            or len(self.get_starttag_text()) > 16384
        ):
            raise ValueError("bulletin parser bound exceeded")
        semantic = {
            "href",
            "rel",
            "name",
            "property",
            "content",
            "datetime",
            "type",
            "id",
        }
        selected = [name for name, _ in attrs if name in semantic]
        if len(set(selected)) != len(selected):
            raise ValueError("duplicate semantic HTML attributes are ambiguous")
        node = _Element(tag, attrs, self.getpos()[0], self.stack[-1])
        self.stack[-1].children.append(node)
        self.elements.append(node)
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        if data.strip():
            self.stack[-1].children.append(data.strip())


class _MetadataDocument(_Document):
    """Locate inert script data in the original decoded source, without trimming."""

    def __init__(self, source):
        super().__init__()
        self.source = source
        self.locations, self.starts, self.ends, self.closed = {}, {}, {}, {}
        self.position_line, self.line_start = 1, 0

    def _offset(self):
        line, column = self.getpos()
        while self.position_line < line:
            self.line_start = self.source.index("\n", self.line_start) + 1
            self.position_line += 1
        return self.line_start + column

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        node = self.elements[-1]
        line, column = self.getpos()
        self.locations[node] = (line, column + 1)
        if tag in ("script", "style"):
            self.starts[node] = self._offset() + len(self.get_starttag_text())

    def handle_endtag(self, tag):
        node = next((node for node in reversed(self.stack) if node.tag == tag), None)
        if node is not None:
            self.closed[node] = "end_tag"
            if node in self.starts:
                self.ends[node] = self._offset()
        super().handle_endtag(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        node = self.elements[-1]
        self.closed[node] = "self_closing_tag"
        if node in self.starts:
            self.ends[node] = self.starts[node]
        _Document.handle_endtag(self, tag)

    def metadata(self):
        leads, counts = [], {"jsonld_script": 0, "meta": 0, "time": 0}
        empty_scripts = whitespace_scripts = unclosed_leads = 0
        for node in self.elements:
            kind, text, representation = None, None, "decoded HTML attributes"
            closure = self.closed.get(node)
            if node.tag == "script" and node.attrs.get("type") == "application/ld+json":
                kind = "jsonld_script"
                text = self.source[
                    self.starts[node] : self.ends.get(node, len(self.source))
                ]
                representation = (
                    "untrimmed UTF-8 decoded script data, no entity expansion"
                )
                empty_scripts += text == ""
                whitespace_scripts += bool(text) and not text.strip()
            elif node.tag == "time":
                kind, text = "time", node.text()
                representation = "normalized visible text with HTML references decoded"
            elif node.tag == "meta":
                name = node.attrs.get("name", node.attrs.get("property", ""))
                if re.search(r"date|time|publish|modified", name, re.I):
                    kind, closure = "meta", "void_tag"
            if kind is None:
                continue
            counts[kind] += 1
            unclosed_leads += closure is None
            if len(leads) < MAX_METADATA_LEADS:
                line, column = self.locations[node]
                leads.append(
                    {
                        "kind": kind,
                        "order": sum(counts.values()),
                        "line": line,
                        "column": column,
                        "attributes": node.attrs,
                        "text": text[:MAX_METADATA_TEXT] if text is not None else None,
                        "text_representation": representation,
                        "text_characters": len(text) if text is not None else None,
                        "text_truncated": text is not None
                        and len(text) > MAX_METADATA_TEXT,
                        "empty": text == "" if text is not None else None,
                        "whitespace_only": bool(text) and not text.strip()
                        if text is not None
                        else None,
                        "closed": closure is not None,
                        "closure": closure or "end_of_input_or_implicit_parent_close",
                    }
                )
        unclosed_raw = len(self.starts.keys() - self.ends.keys())
        complete = unclosed_raw == 0 and unclosed_leads == 0
        total = sum(counts.values())
        return {
            "leads": leads,
            "lead_counts": counts,
            "recognized_lead_count": total,
            "retained_lead_count": len(leads),
            "omitted_lead_count": total - len(leads),
            "truncated_preview_count": sum(lead["text_truncated"] for lead in leads),
            "empty_jsonld_script_count": empty_scripts,
            "whitespace_only_jsonld_script_count": whitespace_scripts,
            "unclosed_lead_count": unclosed_leads,
            "unclosed_script_or_style_count": unclosed_raw,
            "elements_scanned": len(self.elements),
            "scan_complete": complete,
            "jsonld_presence": "present"
            if counts["jsonld_script"]
            else ("absent" if complete else "undetermined_in_incomplete_scan"),
            "previews_complete": complete
            and total == len(leads)
            and not any(lead["text_truncated"] for lead in leads),
        }


def inspect_release(raw, url):
    """Extract displayed facts without asserting publication or first vintage."""
    target, path = release_url(url)
    if not isinstance(raw, bytes) or len(raw) > MAX_BODY_BYTES:
        raise ValueError("bulletin body exceeds the parser byte bound")
    text = raw.decode("utf-8", errors="strict")
    if not re.search(r"</html>\s*$", text, re.IGNORECASE):
        raise ValueError("bulletin HTML is incomplete or unsupported")
    document = _Document()
    document.feed(text)
    document.close()
    nodes = document.elements
    headings = [
        node
        for node in nodes
        if node.tag == "h1"
        and "Freight Transportation Services Index (TSI)" in node.text()
    ]
    expected = (
        rf"(?:The )?{MONTHS[int(target[5:]) - 1]} {target[:4]} "
        r"Freight Transportation Services Index \(TSI\)(?:\s|$)"
    )
    if len(headings) != 1 or not re.match(expected, headings[0].text(), re.I):
        raise ValueError("headline does not identify the requested Freight target")
    for node in nodes:
        if (
            node.tag == "link"
            and node.attrs.get("rel") == "canonical"
            and release_url(node.attrs.get("href"))[1] != path
        ):
            raise ValueError("canonical URL conflicts with the requested bulletin")
    bulletin_ids = {}
    for node in nodes:
        if node.tag == "p" and node.line >= headings[0].line:
            for match in re.finditer(r"\bBTS\s+(\d{2}-\d{1,4})\b", node.text()):
                bulletin_ids["BTS " + match[1]] = node.line
    if len(bulletin_ids) != 1:
        raise ValueError("bulletin identifier is missing or ambiguous")
    bulletin_id, bulletin_line = next(iter(bulletin_ids.items()))
    displayed = {}
    for node in nodes:
        value = (
            " ".join(node.text().split()) if node.tag in ("div", "span", "p") else ""
        )
        if headings[0].line <= node.line <= bulletin_line and re.fullmatch(
            r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), "
            r"[A-Z][a-z]+ \d{1,2}, \d{4}",
            value,
        ):
            day = datetime.strptime(value, "%A, %B %d, %Y")
            if day.strftime("%A") != value.split(",")[0]:
                raise ValueError("displayed bulletin weekday conflicts with its date")
            displayed[day.date().isoformat()] = node.line
    if len(displayed) != 1:
        raise ValueError("displayed bulletin date is missing or ambiguous")
    displayed_date, date_line = next(iter(displayed.items()))
    if bulletin_id[4:6] != displayed_date[2:4]:
        raise ValueError("bulletin identifier year conflicts with the displayed date")
    label = MONTHS[int(target[5:]) - 1][:3] + "-" + target[2:4]
    values = []
    for table in (node for node in nodes if node.tag == "table"):
        rows = [node for node in nodes if node.tag == "tr" and _inside(node, table)]
        if not rows:
            continue
        headers = _cells(rows[0])
        if headers != ["Month", "Freight TSI"]:
            continue
        for row in rows[1:]:
            cells = _cells(row)
            if cells and cells[0] == label:
                if len(cells) != 2 or not re.fullmatch(r"\d+(?:\.\d+)?", cells[1]):
                    raise ValueError(
                        "Freight index row has unsupported units or values"
                    )
                value = float(cells[1])
                if not math.isfinite(value) or value <= 0:
                    raise ValueError("Freight index must be finite and positive")
                values.append(
                    {
                        "value": value,
                        "line": row.line,
                        "table_id": table.attrs.get("id"),
                        "row_label": label,
                    }
                )
    if len(values) != 1:
        raise ValueError("Freight target row is missing or ambiguous")
    metadata, leads = [], {}
    for node in nodes:
        if node.tag == "meta":
            name = node.attrs.get("name", node.attrs.get("property", ""))
            if re.search(r"date|time|publish|modified", name, re.I):
                metadata.append({"line": node.line, "attributes": node.attrs})
        if node.tag == "time" or node.attrs.get("type") == "application/ld+json":
            metadata.append(
                {
                    "line": node.line,
                    "attributes": node.attrs,
                    "text": node.text()[:2048],
                }
            )
        if node.tag == "a" and any(
            word in (node.attrs.get("href", "") + node.text()).lower()
            for word in ("release", "tsi", "bw6n-ddqk")
        ):
            href = node.attrs.get("href", "")
            leads[href] = {
                "url": href[:2048],
                "label": node.text()[:300],
                "line": node.line,
            }
    warning = [
        node.text()[:1000]
        for node in nodes
        if node.tag == "p"
        and "statistics in this release may be revised" in node.text().lower()
    ]
    facts = {
        "schema_version": 1,
        "source_sha256": _sha(raw),
        "target_month": target,
        "series": SERIES,
        "value": values[0]["value"],
        "value_location": values[0],
        "headline": headings[0].text(),
        "bulletin_id": bulletin_id,
        "displayed_bulletin_date": {
            "date": displayed_date,
            "precision": "day",
            "timezone": None,
            "line": date_line,
        },
        "units": None,
        "unit_basis": "The index base needs separately reviewed evidence.",
        "publication_verified": False,
        "first_release_verified": False,
        "metadata_leads": metadata[:32],
        "link_leads": list(leads.values())[:32],
        "mutable_release_warning": warning[:4],
        "limitation": (
            "Displayed date, metadata and retrieval do not prove original publication "
            "or first vintage."
        ),
    }
    if len(json.dumps(facts, allow_nan=False).encode()) > MAX_JSON_BYTES:
        raise ValueError("extracted bulletin facts exceed the output byte bound")
    return facts


def _inside(node, ancestor):
    while node.parent is not None:
        node = node.parent
        if node is ancestor:
            return True
    return False


def _cells(row):
    return [
        " ".join(node.text().split())
        for node in row.children
        if isinstance(node, _Element) and node.tag in ("td", "th")
    ]


def _json(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value

    def nonfinite(value):
        raise ValueError("nonfinite JSON value: " + value)

    return json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)


def _read(path, maximum):
    with Path(path).open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("input exceeds its byte bound")
    return raw


def _write(path, raw):
    with Path(path).open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _write_json(path, value):
    _write(path, (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8"))


def _new_output(path):
    output = Path(path)
    if output.exists() or output.is_symlink():
        raise FileExistsError("output must be a new attempt directory")
    output.mkdir(parents=True, exist_ok=False)
    return output


def _failed(output, error, details):
    try:
        _write_json(
            output / "failed.json",
            {
                "status": "failed",
                "error_type": type(error).__name__,
                "error": str(error)[:1024],
                "details": details,
            },
        )
    except BaseException as evidence_error:
        error.add_note(
            "failed attempt receipt could not be saved: " + str(evidence_error)[:300]
        )


def capture_release(url, output, *, clock=lambda: datetime.now(UTC), open_url=None):
    """Retain bounded response bytes in a new directory, including failed attempts.

    Socket and between-read deadlines are finite controls, not a hard whole-command SLA.
    The default urllib HTTPS opener verifies TLS.
    A test opener is not real transport proof.
    """
    output = _new_output(output)
    receipt = {
        "schema_version": 1,
        "source_url": url[:2048] if isinstance(url, str) else None,
        "body_complete": False,
        "representation": REPRESENTATION,
        "timeout_seconds": TIMEOUT_SECONDS,
        "maximum_body_bytes": MAX_BODY_BYTES,
        "maximum_redirects": MAX_REDIRECTS,
    }
    content = bytearray()
    try:
        _, expected_path = release_url(url)
        receipt["started_at"] = _clock(clock).isoformat()
        redirects = _Redirects(url)
        opener = open_url or build_opener(redirects).open
        request = Request(
            url,
            headers={
                "User-Agent": "FreightForecastReleaseCapture/1.0",
                "Accept": "text/html",
                "Accept-Encoding": "identity",
            },
        )
        transport_error = None
        try:
            response = opener(request, timeout=TIMEOUT_SECONDS)
        except HTTPError as error:
            response, transport_error = error, error
        with response:
            receipt["http_status"] = response.status
            receipt["resolved_url"] = response.geturl()
            receipt["redirects"] = redirects.followed
            receipt["response_headers"] = {
                name: response.headers.get(name, "")[:2048]
                for name in (
                    "Content-Type",
                    "Content-Encoding",
                    "Content-Length",
                    "Date",
                    "Last-Modified",
                    "ETag",
                    "Content-Language",
                    "Location",
                )
            }
            deadline = time.monotonic() + TIMEOUT_SECONDS
            with (output / "body.bin").open("xb") as stream:
                while True:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("response read deadline exceeded")
                    try:
                        chunk = response.read1(
                            min(65536, MAX_BODY_BYTES - len(content) + 1)
                        )
                    except IncompleteRead as error:
                        partial = error.partial[: MAX_BODY_BYTES - len(content) + 1]
                        stream.write(partial)
                        content.extend(partial)
                        raise
                    if not chunk:
                        break
                    stream.write(chunk)
                    content.extend(chunk)
                    if len(content) > MAX_BODY_BYTES:
                        raise ValueError("response exceeded the capture byte bound")
                stream.flush()
                os.fsync(stream.fileno())
            declared = response.headers.get("Content-Length")
            if declared is not None and (
                not declared.isdecimal() or int(declared) != len(content)
            ):
                raise ValueError("response length conflicts with Content-Length")
            receipt["body_complete"] = True
            if transport_error is not None:
                raise transport_error
            if response.status != 200:
                raise ValueError("official bulletin must have HTTP status 200")
            if release_url(response.geturl())[1] != expected_path:
                raise ValueError("response URL conflicts with the requested bulletin")
            if response.headers.get_content_type() != "text/html":
                raise ValueError("official bulletin must declare text/html")
            if (
                response.headers.get("Content-Encoding", "identity").lower()
                != "identity"
            ):
                raise ValueError("compressed response is outside this capture contract")
            if response.headers.get_content_charset() not in ("utf-8", "utf8"):
                raise ValueError("official bulletin must declare UTF-8")
        receipt["retrieved_at"] = _clock(clock).isoformat()
        if _time(receipt["retrieved_at"]) < _time(receipt["started_at"]):
            raise ValueError("retrieval clock precedes request start")
        raw = bytes(content)
        facts = inspect_release(raw, url)
        if _read(output / "body.bin", MAX_BODY_BYTES) != raw:
            raise ValueError("retained bulletin bytes changed before completion")
        receipt.update(
            {
                "status": "captured_for_review",
                "bytes": len(raw),
                "sha256": _sha(raw),
                "decoded_charset": "utf-8",
            }
        )
        receipt["capture_id"] = digest(receipt)
        _write_json(output / "release.json", facts)
        _write_json(output / "capture.json", receipt)
        return receipt
    except BaseException as error:
        receipt.update({"bytes": len(content), "sha256": _sha(content)})
        _failed(output, error, receipt)
        raise


def load_capture(path):
    """Recheck saved capture identities and reparse the same retained bytes."""
    receipt, facts, _ = _capture_buffers(path)
    return receipt, facts


def _capture_buffers(path):
    """Keep the exact buffers that passed the established capture validation."""
    path = Path(path)
    buffers = {}

    def retained(name, maximum):
        buffers[name] = _read(path / name, maximum)
        return buffers[name]

    receipt = _json(retained("capture.json", MAX_JSON_BYTES))
    if not isinstance(receipt, dict):
        raise ValueError("capture receipt must be an object")
    expected = dict(receipt)
    capture_id = expected.pop("capture_id", None)
    if digest(expected) != capture_id or (path / "failed.json").exists():
        raise ValueError("capture is failed or its receipt identity changed")
    if (
        receipt.get("status") != "captured_for_review"
        or receipt.get("http_status") != 200
        or receipt.get("body_complete") is not True
        or receipt.get("representation") != REPRESENTATION
        or receipt.get("decoded_charset") != "utf-8"
    ):
        raise ValueError("capture is not a completed supported response")
    if (
        release_url(receipt.get("source_url"))[1]
        != release_url(receipt.get("resolved_url"))[1]
    ):
        raise ValueError("capture URL identities conflict")
    if _time(receipt.get("retrieved_at")) < _time(receipt.get("started_at")):
        raise ValueError("capture clocks conflict")
    raw = retained("body.bin", MAX_BODY_BYTES)
    if receipt.get("sha256") != _sha(raw) or receipt.get("bytes") != len(raw):
        raise ValueError("retained bulletin bytes do not match the capture")
    facts = inspect_release(raw, receipt["source_url"])
    if _json(retained("release.json", MAX_JSON_BYTES)) != facts:
        raise ValueError("saved bulletin facts differ from retained source bytes")
    return receipt, facts, buffers


def inspect_metadata(capture, output):
    """Supplement supported legacy facts with bounded, unverified metadata leads."""
    output = _new_output(output)
    completion_attempted = False
    details = {}
    try:
        receipt, facts, buffers = _capture_buffers(capture)
        for value in (receipt, _json(buffers["release.json"])):
            if (
                type(value.get("schema_version")) is not int
                or value["schema_version"] != 1
            ):
                raise ValueError(
                    "metadata inspection supports legacy capture schema 1 only"
                )
        details["capture_id"] = receipt["capture_id"]
        document = _MetadataDocument(
            buffers["body.bin"].decode("utf-8", errors="strict")
        )
        document.feed(document.source)
        document.close()
        report = {
            "schema_version": 1,
            "kind": "supplemental_metadata_inspection",
            "status": "unverified_metadata_inspection",
            "canonical_contract": "legacy_release_facts_v1",
            "capture_id": receipt["capture_id"],
            "source_url": receipt["source_url"],
            "source_files": {
                name: {"sha256": _sha(raw), "bytes": len(raw)}
                for name, raw in buffers.items()
            },
            "location_convention": (
                "one based line and Unicode character column in UTF-8 decoded body"
            ),
            "order_convention": "one based recognized metadata order in the document",
            "scan_scope": (
                "HTMLParser meta/time elements and scripts with exact type "
                "application/ld+json, not a browser DOM or every timestamp"
            ),
            "limits": {
                "body_bytes": MAX_BODY_BYTES,
                "elements": MAX_ELEMENTS,
                "depth": MAX_DEPTH,
                "start_tag_characters": 16384,
                "retained_leads": MAX_METADATA_LEADS,
                "text_characters_per_lead": MAX_METADATA_TEXT,
                "output_bytes": MAX_JSON_BYTES,
            },
            "publication_verified": False,
            "first_release_verified": False,
            "json_validated": False,
            "noncanonical": True,
            "admission_performed": False,
            "limitation": (
                "This supplements the legacy canonical preview without changing it. "
                "Metadata is inert unverified text, not publication or first release "
                "proof. Previews may omit or truncate content and are not parsed "
                "JSON objects. "
                "No script, context or linked resource was executed or fetched."
            ),
            **document.metadata(),
        }
        encoded = (json.dumps(report, indent=2, allow_nan=False) + "\n").encode("utf-8")
        if len(encoded) > MAX_JSON_BYTES:
            raise ValueError("supplemental metadata exceeds the output byte bound")
        _write(output / "metadata.partial.json", encoded)
        if _capture_buffers(capture) != (receipt, facts, buffers):
            raise ValueError("capture changed during metadata inspection")
        completion_attempted = True
        os.replace(output / "metadata.partial.json", output / "metadata.json")
        return report
    except BaseException as error:
        if completion_attempted:
            try:
                (output / "metadata.json").unlink(missing_ok=True)
            except BaseException as cleanup_error:
                with suppress(BaseException):
                    error.add_note(
                        "owned metadata completion cleanup failed: "
                        + str(cleanup_error)[:300]
                    )
        try:
            _failed(output, error, details)
        except BaseException as evidence_error:
            with suppress(BaseException):
                error.add_note(
                    "metadata failure evidence could not be saved: "
                    + str(evidence_error)[:300]
                )
        raise


def _issuance(event):
    if not isinstance(event, dict) or event.get("kind") != "issuance":
        raise ValueError("provide the complete original issuance event")
    payload = event.get("payload")
    if not isinstance(payload, dict) or digest(payload) != event.get("payload_id"):
        raise ValueError("original issuance payload identity differs")
    key = event.get("idempotency_key")
    if not _text(key, 4096) or digest(
        {"kind": "issuance", "key": key, "payload_id": event["payload_id"]}
    ) != event.get("event_id"):
        raise ValueError("original issuance event identity differs")
    if event.get("issuance_id") != event["event_id"]:
        raise ValueError("original issuance reference differs")
    validate_issuance(payload, _time(event.get("recorded_at")))
    return payload


def _evidence(value, role):
    if (
        not isinstance(value, dict)
        or value.get("role") != role
        or not _text(value.get("basis"))
        or not _text(value.get("url"), 2048)
    ):
        return False
    try:
        parsed = urlsplit(value.get("url", ""))
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and _hash(value.get("sha256"))
        )
    except ValueError:
        return False


def _review_reasons(review, receipt, facts, issuance):
    reasons = []
    if not isinstance(review, dict):
        return ["reviewed publication, first release and unit evidence is absent"]
    scope = review.get("evidence_scope")
    if (
        type(review.get("schema_version")) is not int
        or review["schema_version"] != 1
        or scope not in ("reviewed_source_declaration", "synthetic_rehearsal")
    ):
        reasons.append("review scope must be an explicit supported declaration")
    if (
        scope == "synthetic_rehearsal"
        and issuance["evidence_kind"] != "synthetic_fixture"
    ):
        reasons.append("synthetic review evidence cannot qualify a real issuance")
    for name, expected in (
        ("source_sha256", receipt["sha256"]),
        ("source_url", receipt["resolved_url"]),
        ("target_month", facts["target_month"]),
        ("value", facts["value"]),
    ):
        if review.get(name) != expected or isinstance(review.get(name), bool):
            reasons.append("review " + name + " does not match captured bytes")
    publication = review.get("publication")
    if not _evidence(publication, "actual_publication"):
        reasons.append("reviewed actual publication evidence is required")
    else:
        try:
            published = _time(publication.get("published_at"))
            offset = publication.get("display_date_offset", "")
            if not isinstance(offset, str) or not re.fullmatch(
                r"[+-](?:0\d|1[0-4]):[0-5]\d", offset
            ):
                raise ValueError("display date offset is unknown")
            minutes = int(offset[1:3]) * 60 + int(offset[4:])
            if minutes > 14 * 60 or not _text(publication.get("timezone_basis")):
                raise ValueError("display date timezone has no justified basis")
            zone = timezone(
                timedelta(minutes=minutes if offset[0] == "+" else -minutes)
            )
            if (
                published.astimezone(zone).date().isoformat()
                != facts["displayed_bulletin_date"]["date"]
            ):
                raise ValueError(
                    "publication conflicts with the displayed date "
                    "in its declared timezone"
                )
            if published > _time(receipt["retrieved_at"]):
                raise ValueError("publication is after capture retrieval")
            if issuance["evidence_kind"] == "real_issuance" and published <= _time(
                issuance["issued_at"]
            ):
                raise ValueError(
                    "real outcome publication must strictly follow issuance"
                )
        except ValueError as error:
            reasons.append(str(error))
    if not _evidence(review.get("first_release"), "original_unrevised_release"):
        reasons.append("independent original first release value evidence is required")
    units = review.get("unit_evidence")
    if (
        not _evidence(units, "series_units")
        or type(units.get("seasonally_adjusted")) is not bool
        or any(
            units.get(name) != expected
            for name, expected in (
                ("series", SERIES),
                ("units", UNITS),
                ("seasonally_adjusted", True),
            )
        )
    ):
        reasons.append(
            "reviewed series, seasonal adjustment and index base evidence is required"
        )
    return reasons


def _bind_proofs(review, review_path, output):
    reasons = []
    if not isinstance(review, dict):
        return reasons
    parent = Path(review_path).resolve(strict=True).parent
    for name, destination in (
        ("publication", "proof-publication.bin"),
        ("first_release", "proof-first-release.bin"),
        ("unit_evidence", "proof-unit-evidence.bin"),
    ):
        evidence = review.get(name)
        if not isinstance(evidence, dict):
            continue
        filename = evidence.get("file")
        if (
            not isinstance(filename, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", filename)
            or filename.endswith(".")
            or re.fullmatch(
                r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", filename, re.I
            )
        ):
            reasons.append(name + " proof file must be a safe basename")
            continue
        source = parent / filename
        if (
            source.is_symlink()
            or not source.is_file()
            or source.resolve().parent != parent
        ):
            reasons.append(
                name + " proof file must be an existing regular file without symlinks"
            )
            continue
        raw = _read(source, MAX_BODY_BYTES)
        _write(output / destination, raw)
        if not raw or _sha(raw) != evidence.get("sha256"):
            reasons.append(name + " proof bytes do not match the declared identity")
    return reasons


def prepare_candidate(
    capture,
    issuance_event,
    key,
    output,
    *,
    review=None,
    clock=lambda: datetime.now(UTC),
):
    """Freeze a review envelope and, only with declared proof, an exact schema payload.

    A supplied event is checked for internal identity.
    Its database history is not authenticated here.
    Use the ledger's separate read only review command before any authorized append.
    """
    output = _new_output(output)
    details = {}
    ready_record_owned = False
    try:
        if not _text(key, 4096):
            raise ValueError("a bounded retained idempotency key is required")
        receipt, facts = load_capture(capture)
        event_raw = _read(issuance_event, MAX_JSON_BYTES)
        event = _json(event_raw)
        issuance = _issuance(event)
        now = _clock(clock)
        if _time(receipt["retrieved_at"]) > now:
            raise ValueError("capture retrieval is after candidate preparation")
        review_raw = _read(review, MAX_JSON_BYTES) if review is not None else None
        declaration = _json(review_raw) if review_raw is not None else None
        reasons = _review_reasons(declaration, receipt, facts, issuance)
        if review_raw is not None:
            reasons.extend(_bind_proofs(declaration, review, output))
        if issuance["target_month"] != facts["target_month"]:
            reasons.append("captured target does not match the original issuance")
        envelope = {
            "schema_version": 1,
            "status": "not_admitted",
            "reasons": reasons,
            "prepared_at": now.isoformat(),
            "idempotency_key": key,
            "issuance_id": event["event_id"],
            "issuance_payload_id": event["payload_id"],
            "capture_id": receipt["capture_id"],
            "source_sha256": receipt["sha256"],
            "target_month": facts["target_month"],
            "displayed_bulletin_date": facts["displayed_bulletin_date"],
            "observation_payload_id": None,
            "observation_file": None,
            "review_sha256": _sha(review_raw) if review_raw is not None else None,
            "limitation": LIMITATION,
            "retry": (
                "Reuse the frozen observation file and this exact key. "
                "A recapture is new evidence."
            ),
        }
        details.update(
            {"issuance_id": event["event_id"], "capture_id": receipt["capture_id"]}
        )
        payload = None
        if not reasons:
            publication = declaration["publication"]
            payload = {
                "issuance_id": event["event_id"],
                "series": SERIES,
                "units": UNITS,
                "target_month": facts["target_month"],
                "vintage_kind": "first_release",
                "value": facts["value"],
                "sha256": receipt["sha256"],
                "source": {
                    "url": receipt["resolved_url"],
                    "retrieved_at": receipt["retrieved_at"],
                    "published_at": publication["published_at"],
                    "publication_verified": True,
                    "publication_basis": (
                        f"Explicit {declaration['evidence_scope']} declaration "
                        f"{envelope['review_sha256']}. "
                        f"Publication: {publication['basis']}. "
                        f"Timezone: {publication['timezone_basis']}. "
                        f"First release: {declaration['first_release']['basis']}. "
                        f"Units: {declaration['unit_evidence']['basis']}. "
                        "Manual evidence truth is not authenticated by this tool."
                    ),
                },
            }
            if len(json.dumps(payload, allow_nan=False).encode()) > MAX_RECORD_BYTES:
                raise ValueError("prepared observation exceeds the ledger record bound")
            envelope.update(
                {
                    "status": "ready_for_ledger_review",
                    "observation_file": "observation.json",
                    "observation_payload_id": digest(payload),
                }
            )
        _write(output / "issuance-event.json", event_raw)
        if review_raw is not None:
            _write(output / "review.json", review_raw)
        if load_capture(capture) != (receipt, facts):
            raise ValueError("capture changed during candidate preparation")
        if payload is not None:
            _write_json(output / "observation.partial.json", payload)
        _write_json(output / "candidate.partial.json", envelope)
        if payload is not None:
            os.replace(output / "observation.partial.json", output / "observation.json")
            ready_record_owned = True
        os.replace(output / "candidate.partial.json", output / "candidate.json")
        return envelope
    except BaseException as error:
        if ready_record_owned:
            try:
                (output / "observation.json").unlink()
            except OSError as cleanup_error:
                error.add_note(
                    "owned candidate cleanup failed: " + str(cleanup_error)[:300]
                )
        _failed(output, error, details)
        raise
