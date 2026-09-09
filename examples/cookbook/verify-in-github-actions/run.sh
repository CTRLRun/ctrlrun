# Extracted by CTRLRun/ctrlrun-docs tools/docs_audit/render_cookbook.py from
# docs/cookbook/verify-in-github-actions.mdx — edit the page, never this file.
ctrlrun verify
ctrlrun verify --json > verify-report.json
python -c "import json; s = json.load(open('verify-report.json'))['summary']; print('applicable', s['applicable'], 'passed', s['passed'], 'not applicable', s['not_applicable'])"
rm -f verify-report.json
