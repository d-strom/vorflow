# Publishing the plugin to your GitHub fork

Replace the placeholders below with your GitHub account and repository names.

## 1. Clone your fork

```bash
git clone https://github.com/<your-user>/<your-fork>.git
cd <your-fork>
```

If the fork already exists locally:

```bash
git checkout main
git pull origin main
```

Use `master` instead of `main` if that is the repository's default branch.

## 2. Create a working branch

```bash
git checkout -b qgis-plugin-0.5.0
```

## 3. Copy the plugin into the repository

Copy the complete `vorflow_qgis` directory from the ZIP into the desired
location in the repository. Keep `metadata.txt`, `__init__.py`,
`vorflow_plugin.py`, `icon.png`, `ATTRIBUTION.md` and the documentation
together.

A QGIS-installable ZIP must have this structure:

```text
vorflow_qgis.zip
└── vorflow_qgis/
    ├── __init__.py
    ├── metadata.txt
    ├── vorflow_plugin.py
    ├── icon.png
    ├── ATTRIBUTION.md
    └── README_installation.txt
```

## 4. Commit and push

```bash
git status
git add vorflow_qgis
git commit -m "Add English QGIS plugin with MODFLOW 6 DISV export"
git push -u origin qgis-plugin-0.5.0
```

## 5. Open a pull request

Open your fork on GitHub. GitHub will normally offer a button to create a pull
request from `qgis-plugin-0.5.0`. Select the appropriate target branch and
describe the plugin changes.

## Optional: publish a release ZIP

Create a tag after merging:

```bash
git checkout main
git pull origin main
git tag -a v0.5.0 -m "Vorflow QGIS plugin 0.5.0"
git push origin v0.5.0
```

Then create a GitHub Release for `v0.5.0` and attach the QGIS-installable ZIP.

Do not commit credentials or personal access tokens. For HTTPS authentication,
use Git Credential Manager or a GitHub personal access token when prompted.
