.PHONY: demo test validate cli-help doctor tiktok-help tiktok-comments-help facebook-help youtube-help analysis-help voice-help incremental-help ledger-help

demo:
	python3 scripts/run_demo.py

test:
	python3 -m unittest discover -s tests -v

validate: test
	python3 scripts/run_demo.py --quiet
	./public-web-census --help >/dev/null
	./public-web-census tiktok --help >/dev/null
	./public-web-census tiktok-comments --help >/dev/null
	./public-web-census facebook --help >/dev/null
	./public-web-census discover --help >/dev/null
	./public-web-census refresh --help >/dev/null
	./public-web-census validate --help >/dev/null
	./public-web-census export --help >/dev/null
	./public-web-census install-skill --help >/dev/null

cli-help:
	./public-web-census --help

doctor:
	./public-web-census doctor

tiktok-help:
	./public-web-census tiktok --help

tiktok-comments-help:
	./public-web-census tiktok-comments --help

facebook-help:
	./public-web-census facebook --help

youtube-help:
	./public-web-census youtube --help

analysis-help:
	python3 scripts/prepare_analysis.py --help
	python3 scripts/apply_analysis.py --help

voice-help:
	python3 scripts/prepare_customer_voice.py --help
	python3 scripts/apply_customer_voice.py --help

incremental-help:
	python3 scripts/merge_incremental.py --help

ledger-help:
	python3 scripts/evidence_store.py --help
