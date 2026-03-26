# Protocols

This directory is only for tiny shared file formats if the runtime actually needs them.

Keep the rule simple:

- if a small JSON file in `.harness/` is enough, use that
- do not build a large protocol layer in advance
- do not move core runtime understanding out of the scheduler and agent docs
