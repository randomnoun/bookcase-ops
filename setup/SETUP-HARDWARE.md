# bookcase-ops hardware

So these are the bits of kit I'm configuring with this project:

# The bookcase

It's metal and it's got 5 shelves on it.

# bnenas04

A ZFS storage server, running TrueNAS SCALE ( based on Linux, which is a step up from my earlier nas servers which ran on FreeNAS/FreeBSD ). 

Everything below is from umart.com.au, unless mentioned otherwise.

* SilverStone Black DS380 8 Bay Hot Swap SFF Chassis, $249.00 ( mwave.com.au )
* C2750D4I mobo, $697.40 ( wisp.net.au )
   * this was initially a C2550D4I for $608.06 but they didn’t have it in stock, so was forced to upgrade
   * is one of the few motherboards around with a huge number of SATA ports on it. In retrospect maybe I could have gone something cheaper and used some PCIe cards to add some slots.
* 8x Seagate Barracuda 8TB ST8000DM004 Desktop 3.5IN HDD, $1,592.00
* Silicon Power A55 256GB TLC 3D NAND 2.5in SATA III SSD, $33.00
   * for the boot drive. You used to be able to run FreeNAS off a usb stick, but that's not supported for TrueNAS.
* Cooler Master V 550W 80+ Gold SFX Power Supply, $134.00
* 4x 8gb SP016GLLTU160N22 DDR3L 1600MHz PC3-12800 1.35V CL11, $178.00
* Generic Internal USB 2.0 (MB-F) to USB3.0 19pin Adaptor Cable, $3.00
* ATEN CS84U 4 Port KVM, $163.00 ( scorptec.com.au )
* Intel Optane Memory 16 GB $24.99 ( ebay )
   * for the ZFS SLOG
* PCIE to M2/M.2 Adapter PCI Express X4 X8 X16 NVME M.2 SSD PCIE Expansion Card , $12.99 ( ebay )
   * so that I could fit the optane memory in there

You need that SLOG by the way, otherwise NFS runs slower than a 3600 baud modem for some tasks.

Those 8 drives are configured in a raidz2 volume, so 2 of them provide resiliency in the case of hardware failures.

Total: 3087.38

# bnehyp05

The hypervisor, for running virtual machines and containers. 

* Cooler Master MT Case N200, $63.00
* B360M-PRO-VDH mobo, $123.70 ( amazon )
* Intel Core i7 8700K Six Core LGA 1151 3.7 GHz CPU Processor, $619.00
* Thermaltake Litepower 500W OEM ATX PSU, $45.00
* Thermaltake Contac Silent 12 CPU Cooler - AM4 Support, $45.00
* Logitech M90 Optical Mouse, $9.00
* Samsung 2TB 860 QVO 2.5in SATA SSD, $330.00
   * think I put an old spinning rust SATA drive in here as well which I use for occasional backups 
* G.SKILL Ripjaws V Series 64GB (4 x 16GB) 288-Pin DDR4 SDRAM DDR4 2800 (PC4 22400) Desktop Memory Model F4-2800C14Q-64GVK, $536.00 ( newegg )

Total: 1770.70 

# bnehyp02

An older hypervisor that is also running bind9 and isc-dhcp-server

* DELL PRECISION T1600, XEON E31245 (3.3 GHZ), 16 GB, 1 TB $420 ( ebay )
   * Got this back in 2017 and it's mostly retired from active duty   

Total: 420.00

# That's it ?

Yep. Well, there's a network switch, and a KVM attached to an old monitor/keyboard, and some other miscellaneous crap, but the boxes listed above is what this particular project is configuring. 

