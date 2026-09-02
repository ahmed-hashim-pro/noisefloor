# Example: rag-knowledge-agent

`suite.yaml` targets
[rag-knowledge-agent](https://github.com/ahmed-hashim-pro/rag-knowledge-agent)
unmodified, through its `rag ask --json` command. Five cases cover both
on-topic questions (expecting `high`/`medium` confidence and citations drawn
from the sample corpus) and off-topic or out-of-policy questions (expecting
the model's refusal sentence and `low` confidence).

## Running it

This suite does not run standalone — it needs a sibling checkout of
`rag-knowledge-agent`, ingested once:

```bash
cd ../rag-knowledge-agent
pip install -e .
rag ingest sample_corpus
cd ../noisefloor
noisefloor check examples/rag-knowledge-agent/suite.yaml
```

`target.cwd` in `suite.yaml` is `../../../rag-knowledge-agent`, resolved
relative to the suite file itself, so this only works if `rag-knowledge-agent`
lives as a sibling directory to this `noisefloor` checkout.

## API key

Reading the committed baseline needs nothing — no key, no network. Only
*regenerating* it does: each `noisefloor check` or `noisefloor run` invokes
the target once per case per repeat, which at this suite's defaults (5 cases
x 5 repeats) is roughly 25 calls to whatever model `rag-knowledge-agent` is
configured to use, and needs `ANTHROPIC_API_KEY` set in the environment
`rag ask` runs in.

## The `allowed` lists

The `json_path_subset` scorers' `allowed` lists were set from a real,
offline retrieval run against the sample corpus — see the comment at the top
of `suite.yaml`. They are deliberately not widened past what was measured.
