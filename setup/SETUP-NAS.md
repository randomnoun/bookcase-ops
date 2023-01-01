# bookcase-ops NAS

This project has been tested on the free version of [TrueNAS SCALE](https://www.truenas.com/docs/scale/).

TrueNAS is the successor to FreeNAS, and 'TrueNAS SCALE' is the version of TrueNAS that runs on linux, which you are definitely going to prefer to FreeBSD the first time you want to run 
something on there that you need to install yourself. 

The TrueNAS server in this project is called `bnenas04.dev.randomnoun`, so if you want to call it something different then do a search & replace on that.

# Installation

Follow the steps at [https://www.truenas.com/docs/scale/gettingstarted/](https://www.truenas.com/docs/scale/gettingstarted/)

Once you've burnt an ISO onto a USB stick and used it to install the OS, you should be able to login via the web UI to administer the nas, which is when I started taking screenshots of things.

* The TrueNAS dashboard - hit the 'Check for updates' button to update to the latest

![](image/truenas-1-dashboard.png)

* Select 'storage' on the left hand side, then 'Create pool' to create the storage pool:

[truenas-2-storage]
   
* I've named the pool 'raidvolume', and selected all 8GB disks to comprise the storage, in a raid-z2 formation:

[truenas-3-storage-disk]

* Create a dataset within the pool

[truenas-5-dataset]

* I've named the dataset 'compressed' as it will have lz4 compression on by default:

[truenas-5-dataset-2]

* Within the raidvolume pool, create a `k8s` dataset ( for kubernetes ), then a `bnekub02` dataset within that ( for the bnekub02 cluster ), and an `nfs` dataset within that ( for democratic-csi volumes )
   * I did this a bit later than the other steps, ignore that 'ix-applications' folder.

[truenas-8-dataset-3]

* Click 'Sharing' on the left hand side and create an SMB share
* I've named the share 'raidvolume' and pointed it to the /mnt/raidvolume folder

[truenas-6-smb-share]

* Enable the SMB service

[truenas-6-smb-share-2]

* Enable a couple more system services ( SSH and S.M.A.R.T. checks )
* You'll probably need NFS as well for kubernetes later on.
   * Enable NFSv4
   * Enable NFSv3 ownership model for NFSv4

[truenas-7-services.png]
[truenas-7-services-2.png]

* Create a non-root user; mine is 'knoxg'

[truenas-4-user]
[truenas-4-user-2]

----

# Adding a SLOG vdev to the pool

So after going through that and setting up the rest of the cluster, I found performance to be pretty dismal - here's an issue raised on the democratic-csi github tracker, with 
some I/O performance stats.

The solution was to add a ZFS SLOG drive. Here are the steps to do that:

* Under Storage -> Disks, check the disk is appearing. I'm using a small Optane NVMe2 SSD, which can apparently survive power outages a bit more resiliently than non-Optane drives.

[truenas-9-slog-4.png]

* Hit that icon on the raidvolume pool and select 'Add vdevs' 

[truenas-9-slog]

* Hit the 'Add Vdev' button up the top and select 'Log'

[truenas-9-slog-2]

* Add the drive to the 'Log Vdev' list, select 'Stripe' configuration, check 'Force' and add the vdev. You'll get a few more confirmation warnings and dialogs to click through.

[truenas-9-slog-3]

