# Data licensing and provenance

The source code in this repository is licensed separately under AGPL-3.0. Third-party datasets in this directory are **not relicensed under AGPL-3.0**.

## UCI Recipe Reviews and User Feedback (dataset 911)

Files derived from or containing this dataset:

- `uci_recipe_reviews_full.csv.gz` — vendored copy of the UCI dataset used by the local recipe-review browser.
- `uci_recipe_reviews_100.json` — TestGraph import bundle containing a representative 100-review subset plus TestGraph transformation/provenance metadata.

Source: **Recipe Reviews and User Feedback**, UCI Machine Learning Repository, dataset 911.

Authors: Amir Ali, Stanislaw Matuszewski, and Jacek Czupyt (2023).

DOI: `10.24432/C5FG95`

Licence: **Creative Commons Attribution 4.0 International (CC BY 4.0)**.

Source page: `https://archive.ics.uci.edu/dataset/911/recipe%2Breviews%2Band%2Buser%2Bfeedback%2Bdataset`

The UCI source includes reviewer/user identifiers and user names as dataset fields. Those values are third-party dataset content, not TestGraph account data. TestGraph does not claim ownership of them. Redistribution and adaptation of the dataset are governed by CC BY 4.0 and require the attribution above.

The `uci_recipe_reviews_100.json` bundle records the source dataset ID, DOI, licence and attribution in its own metadata and retains source-level provenance for transformed records.
