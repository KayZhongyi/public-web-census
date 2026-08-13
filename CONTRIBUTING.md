# Contributing

Thanks for helping make Competitor Census more useful and safer.

Good first contributions include fictional demo datasets, report themes, schema validators, documentation fixes, and collection adapters that use authorized access and respect platform controls.

Before opening a pull request:

1. Do not include credentials, cookies, private URLs, personal data, client/employer names, or proprietary datasets.
2. Use fictional or explicitly licensed examples.
3. Do not add CAPTCHA bypasses, stealth evasion, rate-limit bypasses, or authentication circumvention.
4. Normalize adapter output to the documented evidence-bundle schema.
5. Run `python3 -m unittest discover -s tests -v` and `python3 scripts/run_demo.py`.
6. Explain the target platform/tool, tested environment, limitations, and evidence fields in the pull request.

Live-adapter tests must use a fake local collector in CI. Do not commit real target output, cookies, session material, or downloaded media.

The TikTok and Facebook connectors intentionally depend on OpenCLI as an external browser bridge. Contributions belong in this repository when they implement Competitor Census commands, schemas, checkpoints, validation, or reporting. Changes to the generic browser bridge should go upstream to OpenCLI instead of copying or rebranding its source here.

Open an issue before a large architectural change so the scope can be agreed first.
