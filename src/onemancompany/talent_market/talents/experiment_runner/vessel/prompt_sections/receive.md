# Experiment Task Format

Prefer a JSON task config with:

```json
{
  "command": "python train.py",
  "working_dir": "/path/to/project",
  "monitor_backend": "auto",
  "metric_dir": "runs",
  "target_metric": "val_acc",
  "target_value": 0.9,
  "budget_hours": 4,
  "kill_rules": {
    "nan_loss": true,
    "plateau_patience": 5,
    "gpu_idle_minutes": 10,
    "disk_min_free_gb": 2
  }
}
```

For auto-research scripts, write newline-delimited JSON metrics to
`metrics.jsonl`; each line can be either flat (`{"step": 1, "val_acc": 0.8}`)
or nested (`{"step": 1, "metrics": {"val_acc": 0.8}}`).
