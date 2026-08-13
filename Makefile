.PHONY: demo test validate cli-help facebook-help youtube-help analysis-help voice-help incremental-help

demo:
	python3 scripts/run_demo.py

test:
	python3 -m unittest discover -s tests -v

validate: test
	python3 scripts/run_demo.py --quiet
	./competitor-census --help >/dev/null
	./competitor-census facebook --help >/dev/null

cli-help:
	./competitor-census --help

facebook-help:
	./competitor-census facebook --help

youtube-help:
	./competitor-census youtube --help

analysis-help:
	python3 scripts/prepare_analysis.py --help
	python3 scripts/apply_analysis.py --help

voice-help:
	python3 scripts/prepare_customer_voice.py --help
	python3 scripts/apply_customer_voice.py --help

incremental-help:
	python3 scripts/merge_incremental.py --help
