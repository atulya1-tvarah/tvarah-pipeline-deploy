# Training And Release Guide

## Purpose

This document explains:

- how the BERT training pipeline was run on extracted resume JSON files
- which trained models were selected as the final release artifacts
- which experimental folders were intentionally removed to avoid confusion
- what should be committed to Git for deployment

## Training Data Source

The training source used for this release was:

`E:\Dev\profile-extraction-ml-poc\profile-extraction-ml-poc-deployment\ResumeParserAgent\drive_imports\json`

The extracted JSON files were converted into task-specific JSONL exports for:

- `role_family`
- `dna_fit`
- `project_type`
- `skill_depth`

## Training Strategy

The trainer was updated to support a CPU-friendly fast preset so that training is practical on a non-GPU machine.

### Fast preset behavior

- shorter sequence lengths per task
- larger batch size where CPU memory allowed it
- fewer epochs for tasks that already had strong signal
- per-task label rebalancing
- optional collapse of weak `skill_depth` classes

### Rebalancing behavior

The trainer now rebalances the training split by:

- capping oversized classes
- upsampling small classes
- keeping validation untouched so reported metrics stay honest

For `skill_depth`, `FOUNDATIONAL` is collapsed into `HANDS_ON` by default for training stability.

## Final Selected Models

The final release uses the best model chosen per task from the training experiments:

- `role_family`: from `trained_models_balanced_fast_drive_imports\role_family`
- `dna_fit`: from `trained_models_v4_drive_imports\dna_fit`
- `project_type`: from `trained_models_fast_drive_imports\project_type`
- `skill_depth`: from `trained_models_balanced_fast_drive_imports\skill_depth`

These were consolidated into one final deployable directory:

`E:\Dev\resume_intelligence\trained_models_release_2026_04_18`

## Final Validation Metrics

### Role Family

- accuracy: `50.4%`
- macro F1: `0.226`

### DNA Fit

- accuracy: `59.1%`
- macro F1: `0.300`

### Project Type

- accuracy: `66.4%`
- macro F1: `0.519`

### Skill Depth

- accuracy: `79.8%`
- macro F1: `0.414`

## Why These Versions Were Chosen

### Role Family

The balanced fast version slightly improved macro F1 and kept accuracy roughly stable, so it was chosen over the slower earlier run.

### DNA Fit

The balanced fast retrain reduced performance, so the earlier `v4` run was kept as the final choice.

### Project Type

The CPU-fast run produced the best practical result with strong validation accuracy and much faster turnaround.

### Skill Depth

This task was highly imbalanced. The final selected model uses balanced training and collapsed labels, which made the result far more useful than a naive run dominated by `HANDS_ON`.

## Deployment Decision

Only the following should go to Git for deployment:

- `trained_models_release_2026_04_18`
- code changes required to use that folder
- documentation files needed by the team

Experimental training folders and temporary export folders were removed so there is one clear release candidate.

## Git Publishing Note

The final model files are too large for normal Git blobs. Push the release bundle using Git LFS.

Recommended LFS patterns:

- `*.safetensors`

Checkpoints and optimizer snapshots should not be included in the release folder. Only inference-ready files should remain.

## Runtime Configuration

The application should use:

`TRAINED_MODELS_DIR=trained_models_release_2026_04_18`

This makes the runtime load the consolidated final models instead of older or experimental folders.

## Reproducing Training Later

### 1. Build exports

```powershell
.\.venv\Scripts\python.exe training_data_builder.py "E:\Dev\profile-extraction-ml-poc\profile-extraction-ml-poc-deployment\ResumeParserAgent\drive_imports\json" --output-dir "E:\Dev\resume_intelligence\training_exports_drive_imports"
```

### 2. Train task-specific models

Example:

```powershell
.\.venv\Scripts\python.exe train_bert.py --data-dir "E:\Dev\resume_intelligence\training_exports_drive_imports" --task role_family --output-dir "E:\Dev\resume_intelligence\trained_models_balanced_fast_drive_imports"
```

The trainer now defaults to the CPU-fast preset, so additional flags are only needed if you want to override defaults.

## Git Recommendation

Commit:

- source code
- `.env` change for `TRAINED_MODELS_DIR`
- `trained_models_release_2026_04_18`
- this document

Do not commit:

- intermediate `trained_models_*` experiment folders
- temporary `training_exports_*` folders

## Summary

The folder has been reduced to one final release model set so deployment is clear and reproducible. The strongest practical gains are in `project_type` and `skill_depth`, while `role_family` improved modestly and `dna_fit` kept the better earlier run.
