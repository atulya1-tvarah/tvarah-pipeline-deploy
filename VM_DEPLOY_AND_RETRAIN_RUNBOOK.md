# VM Deploy And Retrain Runbook

## Purpose

This document explains how to:

- pull the latest project from GitHub onto a VM
- install dependencies
- configure environment variables
- start the resume analysis application
- rebuild training exports
- retrain the BERT models again

## Assumptions

- the VM is a Windows machine
- Git is installed
- Git LFS is installed
- Python 3.11 or 3.12 is installed
- the VM can access GitHub
- the VM can access the resume JSON source folder or a copied dataset

Repository:

`https://github.com/adityabikramtvarah/Resume-analysis-.git`

## 1. Clone The Repository On The VM

Open `cmd` and run:

```cmd
cd /d E:\
git lfs install
git clone https://github.com/adityabikramtvarah/Resume-analysis-.git
cd Resume-analysis-
```

If the repository is already on the VM:

```cmd
cd /d E:\Resume-analysis-
git pull origin main
git lfs pull
```

## 2. Create The Virtual Environment

```cmd
cd /d E:\Resume-analysis-
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-ml.txt
```

## 3. Configure Environment Variables

Create the local environment file:

```cmd
copy .env.example .env
```

Then edit `.env` and fill in real keys if you want LLM-backed scoring and summaries.

Important runtime setting:

```env
TRAINED_MODELS_DIR=trained_models_release_2026_04_18
```

## 4. Start The Application On The VM

### FastAPI

```cmd
cd /d E:\Resume-analysis-
.venv\Scripts\activate
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

Open:

- `http://localhost:8000`
- `http://<vm-ip>:8000`

### Optional Streamlit Dashboard

```cmd
cd /d E:\Resume-analysis-
.venv\Scripts\activate
streamlit run dashboard.py
```

## 5. Pull New Changes Later

When new code is pushed to GitHub:

```cmd
cd /d E:\Resume-analysis-
git pull origin main
git lfs pull
```

If Python dependencies changed:

```cmd
.venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-ml.txt
```

## 6. Rebuild Training Exports

Use the extracted resume JSON folder as the source.

Example source folder:

`E:\Dev\profile-extraction-ml-poc\profile-extraction-ml-poc-deployment\ResumeParserAgent\drive_imports\json`

Build the training exports:

```cmd
cd /d E:\Resume-analysis-
.venv\Scripts\activate
python training_data_builder.py "E:\Dev\profile-extraction-ml-poc\profile-extraction-ml-poc-deployment\ResumeParserAgent\drive_imports\json" --output-dir "E:\Resume-analysis-\training_exports_drive_imports"
```

This creates task-specific export files for:

- `role_family`
- `dna_fit`
- `project_type`
- `skill_depth`

## 7. Retrain The Models Again

The trainer now defaults to a faster CPU-friendly preset and balanced training behavior.

### Role Family

```cmd
cd /d E:\Resume-analysis-
.venv\Scripts\activate
python train_bert.py --data-dir "E:\Resume-analysis-\training_exports_drive_imports" --task role_family --output-dir "E:\Resume-analysis-\trained_models_retrain"
```

### DNA Fit

```cmd
cd /d E:\Resume-analysis-
.venv\Scripts\activate
python train_bert.py --data-dir "E:\Resume-analysis-\training_exports_drive_imports" --task dna_fit --output-dir "E:\Resume-analysis-\trained_models_retrain"
```

### Project Type

```cmd
cd /d E:\Resume-analysis-
.venv\Scripts\activate
python train_bert.py --data-dir "E:\Resume-analysis-\training_exports_drive_imports" --task project_type --output-dir "E:\Resume-analysis-\trained_models_retrain"
```

### Skill Depth

```cmd
cd /d E:\Resume-analysis-
.venv\Scripts\activate
python train_bert.py --data-dir "E:\Resume-analysis-\training_exports_drive_imports" --task skill_depth --output-dir "E:\Resume-analysis-\trained_models_retrain"
```

## 8. Review The New Training Outputs

Each task writes into its own folder under:

`E:\Resume-analysis-\trained_models_retrain`

Check each task folder for:

- `config.json`
- `model.safetensors`
- `tokenizer.json`
- `tokenizer_config.json`
- `training_metadata.json`

Open `training_metadata.json` to review:

- train example count
- validation example count
- accuracy
- macro F1
- rebalance summary

## 9. Promote A New Final Release Bundle

After retraining, select the best model per task and copy only the final chosen task folders into one release directory.

Example:

```cmd
mkdir trained_models_release_next
xcopy /E /I /Y trained_models_retrain\role_family trained_models_release_next\role_family
xcopy /E /I /Y trained_models_retrain\dna_fit trained_models_release_next\dna_fit
xcopy /E /I /Y trained_models_retrain\project_type trained_models_release_next\project_type
xcopy /E /I /Y trained_models_retrain\skill_depth trained_models_release_next\skill_depth
```

Important:

- keep only inference files in the final release bundle
- remove checkpoint folders
- do not keep optimizer snapshots in the release folder

## 10. Point The App To The New Release Bundle

Update `.env`:

```env
TRAINED_MODELS_DIR=trained_models_release_next
```

Restart the app after changing this.

## 11. Push The Updated Models And Code Back To GitHub

```cmd
cd /d E:\Resume-analysis-
git pull origin main
git lfs pull
git add -A
git commit -m "Update trained models and deployment config"
git push origin main
```

## 12. Recommended Validation After Retraining

- open the app and analyze 3 to 5 known resumes
- verify name, contact fields, and candidate overview still look correct
- verify score breakdown tiles are consistent with total score
- verify skill evidence no longer shows unwanted checkpoint-style artifacts
- review `training_metadata.json` before replacing the current release bundle

## 13. Troubleshooting

### Git LFS files are missing

```cmd
git lfs install
git lfs pull
```

### The app does not find models

Check `.env`:

```env
TRAINED_MODELS_DIR=trained_models_release_2026_04_18
```

or your newer promoted release folder.

### Training is too slow

The current trainer already uses the faster CPU-friendly preset by default. Keep the current defaults unless there is a strong reason to change them.

### Git push fails after retraining

Make sure:

- Git LFS is installed
- `.safetensors` files are tracked
- the VM can resolve `github.com`

## Summary

This runbook gives the operational path for:

1. cloning the repo to a VM
2. starting the app
3. regenerating exports
4. retraining the four classification tasks
5. promoting a new final release bundle
6. pushing the updated release back to GitHub
