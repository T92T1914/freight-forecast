# Model and history compatibility

The time trend is a count of months from the first observation used to build the
training features. That date is saved in the artifact as `trend_origin`.
Serving uses the saved origin, including when the loaded history is a shorter
window. Removing older observations must not reset the trend to zero.

The lag and rolling inputs still need contiguous monthly observations. Keeping
the most recent 12 observed months supports only the next unobserved month.
Keeping more history also supports historical requests after their lag warmup.
The service rejects history that begins before the saved training origin.

Older artifacts do not record this origin. They fail startup with an explicit
retraining instruction because guessing from a replacement CSV could return a
different forecast with an apparently healthy service. Run `python -m src.train`
with the intended complete training dataset before upgrading the service. This
uses the existing seasonal baseline acceptance check and replaces the saved
artifact only if training passes. Updating observations alone does not retrain.

The regression tests compare forecasts and their complete feature rows after
removing 1, 12, 48 and 84 old months. API tests train a temporary artifact and load it
through the real startup path; they do not replace the local serving artifact.
