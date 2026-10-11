# Publishing Checklist

The Studio source is intended for a code-only GitHub repository. Do not push
the tool as a "complete" or "crash-free" ROM builder while diagnostic builds
can still fail and image-baked text is not fully covered.

Before publishing a development branch:

1. Include every local Python dependency imported by `policenauts_studio.py`,
   `policenauts_studio_cli.py`, `policenauts_studio_core.py`, and
   `policenauts_studio_geometry.py`. Several current experimental assembler
   modules are not yet tracked in the baseline repository; publishing only the
   new GUI files would create a broken clone.
2. Run `py -m unittest discover -s tests -q`, `py -m py_compile` on the Studio
   entry points, and `git diff --check`.
3. Verify a private source project without an external catalog, compare the
   reconstructed catalog to the audited master, and check a bit-exact source
   round-trip. Do not upload their output folders.
4. Review `git status --short` and the staged diff before committing. Stage
   only code, tests, documentation, and the text-free geometry map. Never
   stage BIN/CUE/ISO, private master/translation JSON, extracted assets,
   font PNG/raw files, save states, screenshots, or generated reports.
5. Describe the branch as **experimental**. Static checks do not replace a
   full game playthrough, and a successful diagnostic build is not a release.

The user's current workspace has pre-existing unrelated modifications. Do not
discard or commit those indiscriminately while preparing the Studio branch.
