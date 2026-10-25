# bookcase-ops rustfs

This bit describes how to install and configure a [rustfs](https://rustfs.com/) storage server on the NAS. 

Restic is an open-source implementation of the AWS S3 protocol; I was running minio for a while but that's been dropped to restfs it is. 

It runs on the nas ( bnenas05 ); the frontend is unsecured for now.

# Installation

I used the built-in 'restfs' app, so it's all mostly point-and-click

## Datasets

First create the dataset 

1. Open TrueNAS webapp at http://bnenas05.dev.randomnoun/
2. Navigate to Datasets.
3. Select the exiseting 'compressed' dataset, and create a sub-dataset called 'datasets'
4. Accept the defaults and click Save
4. Select the 'datasets' dataset, and create a sub-dataset called 'restfs'. Change the Dataset Preset to Apps.
6. Click Save.

## The app

1. Navigate to the Apps section from the sidebar.
2. Search for RustFS and click Install.
3. Configure the following deployment settings:

| Section | Setting | Value |
|--|--|--|
| Application Name | Name | Leave default (rustfs) |
| RustFS Configuration | Deployment Mode Single disk node (SNSD) (Default for single servers) |
| | Access Key | `knoxg` |
| | Secret Key | `super-secret-password` |
| Storage Configuration | Type | Select Host Path |
| | Host Path | Browse and pick the `rustfs` dataset created earlier |

4. Leave resource limits at default (it typically requires a maximum of 2 CPUs and 4GB RAM) and click Install.

5. Verification and Access

Once the status changes to Active:
• Web UI (Management Console): Open your browser and go to http://bnenas05.dev.randomnoun:30293. Log in using your configured Access Key and Secret Key.

