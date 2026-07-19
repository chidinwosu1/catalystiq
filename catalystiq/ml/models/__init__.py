"""Model-family training and evaluation pipelines (Models 1-5).

Every module here imports scikit-learn LAZILY (inside functions), so this
package imports cleanly without sklearn installed - the disabled inference
endpoint and all pure utilities keep working. Training requires sklearn and
is gated behind ENABLE_ML_TRAINING; nothing here is invoked on a user request
path unless an approved artifact exists.
"""
