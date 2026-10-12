"""The labels JEV reads, one module per family, registered in ``registry.py``.

A family owns a fixed list of labels (``LABELS``) and, optionally, the sleep gates of the questions it
can judge (``GATES``), and exposes one builder, ``build_<family>_labels(scene) -> LabelSet``. The
registry runs every family on the same Scene and joins what they wrote; a family may write only the
labels and gates it owns, so two families can be built by two people without touching one file.

The rules every family keeps:

* Omit, never null. A label that cannot be measured is left out with its reason (``LabelSet.omit``);
  the packer skips every question that reads it. A label a family owns but does not write yet is
  omitted by the registry as not built.
* Point in time. Everything comes from the Scene: bars that finished before ``scene.now``, rows up to
  it, prior sessions strictly before the day, market values once they were known.
* No prices in labels. Distances are in sigma, shares in percent, strikes described and never named.
* Every threshold is a cuts.py constant, and the sentence carries the figure and the rule it was
  judged against, so JEV never compares numbers.

Shared pieces: ``measures`` (bar windows and reads), ``rulers`` (the tape unit), ``ranks`` (a value
against the same minute of the prior sessions), ``words`` (how a number is written in a sentence).
"""
