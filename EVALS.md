### Retrieval Evaluation

Last updated: 2026-09-28.

### Threshold calibration

The provisional best-performing gates are:

```dotenv
SAGE_JEV_RELEVANCE_SCORE_THRESHOLD=1.9
SAGE_JEV_RELEVANCE_CONFIDENCE_THRESHOLD=0.52
```

The selection rule was to maximize irrelevant-file removal while retaining every
gold file found by deterministic retrieval.

| Thresholds (score / confidence) | Retrieved correct files retained | Irrelevant files removed | Average noise after Jev | Average noise reduction | Average post-Jev correct recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| Previous default `2.0 / 0.5` | 91.67% | 82.93% | 30.21% | 35.85 pp | 76.56% |
| Selected `1.9 / 0.52` | 100.00% | 87.80% | 26.04% | 40.02 pp | 82.81% |
