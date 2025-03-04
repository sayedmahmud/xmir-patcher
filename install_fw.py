#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import types
import tarfile
import lzma
import ctypes

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import gateway
from gateway import die
import read_info
import activate_boot
from devtree import *
import fdt


UIMAGE_MAGIC = b"\x27\x05\x19\x56"
FIT_MAGIC    = FDT_MAGIC
HSQS_MAGIC   = b"hsqs"
UBI_MAGIC    = b"UBI#"
UBIv1_MAGIC  = UBI_MAGIC + b"\x01\x00\x00\x00"


gw = gateway.Gateway()
if not gw.device_name:
  die("Xiaomi Mi Wi-Fi device not found! (IP: {})".format(gw.ip_addr))

img_write = True  # used for testing

dn_dir = 'firmware/'
fn_local = None
dn_tmp = 'tmp/fw/'
fn_kernel = dn_tmp + 'kernel.bin'
fn_rootfs = dn_tmp + 'rootfs.bin'
fn_fitubi = dn_tmp + 'fitubi.bin'

os.makedirs(dn_dir, exist_ok = True)
os.makedirs(dn_tmp, exist_ok = True)

imglist = []
fn_list = [f for f in os.listdir(dn_dir) if os.path.isfile(os.path.join(dn_dir, f))]
for i, fname in enumerate(fn_list):
  fname = dn_dir + fname
  fsize = os.path.getsize(fname)
  if fsize < 256*1024:
    continue
  with open(fname, "rb") as file:
    data = file.read(256)
  img = types.SimpleNamespace()
  img.fn = fname
  img.type = None
  if data[:4] == b'HDR1' or data[:4] == b'HDR2':
    img.type = 'stock'
  if data[:10] == b"sysupgrade":  # TAR
    img.type = 'sysupgrade'
  if data[:4] == UIMAGE_MAGIC:    # uImage 
    img.type = 'factory'
  if data[:4] == FIT_MAGIC:       # factory squashfs image  
    img.type = 'factory'
  if data[:8] == UBIv1_MAGIC:     # rootfs_ubi
    img.type = 'rootfs'
  if img.type:
    if len(imglist) == 0:
      print('Image files in directory "{}":'.format(dn_dir))
    print('  "{}"'.format(fname))
    imglist.append(img)

if len(imglist) <= 0:
  die('The firmware was not found in the "{}" folder!'.format(dn_dir))

c_stock = len([i for i in imglist if i.type == 'stock'])
c_sysupgrade = len([i for i in imglist if i.type == 'sysupgrade'])
c_factory = len([i for i in imglist if i.type == 'factory'])
c_rootfs = len([i for i in imglist if i.type == 'rootfs'])

if c_stock > 1 or c_factory > 1 or c_rootfs > 1 or c_sysupgrade > 1:
  die('Too many different files in directory {}'.format(dn_dir))

if c_stock and c_sysupgrade and (c_factory or c_rootfs):
  die('Too many different files in directory {}'.format(dn_dir))   

if c_rootfs and c_factory == 0:
  die('Kernel image not found! (only rootfs is present)')

dev = read_info.DevInfo(verbose = 0, infolevel = 1)
cpuarch = dev.info.cpu_arch
if cpuarch not in 'mips armv7 arm64':
  die("Currently support only MIPS, ARMv7, ARM64 arch!")

class ImgHeader():
  size = None      # Image Data Size
  os = None        # OS type: IH_OS_LINUX = 5
  arch = None      # CPU arch: IH_CPU_MIPS = 5, IH_CPU_ARM = 2
  type = None      # Image Type: IH_TYPE_KERNEL = 2
  comp = None      # IH_COMP_NONE = 0, IH_COMP_GZIP = 1, IH_COMP_BZIP2 = 2, IH_COMP_LZMA = 3, IH_COMP_XZ = 5

class Image():
  type = None      # kernel or rootfs
  ostype = None    # 'stock', 'openwrt', 'padavan', 'pandorabox', etc
  hdr = ImgHeader()
  addr = None
  addr2 = None     # for kernel_stok/kernel_dup
  fn_local = None
  fn_remote = None
  data = None
  data2 = None     # unpacked image
  fit = False
  dtb = None       # device-tree
  cmd = None

kernel = Image()
rootfs = Image()

if c_sysupgrade:
  die("SysUpgrade firmware (TAR archives) are not supported!")
  '''
  fname = imglist[0].fn
  file = tarfile.open(fname)
  file.extractall(dn_tmp)  
  flist = file.getnames() 
  for fn in flist:
    if fn.find('/') > 0:
      bn = os.path.basename(fn)
      if bn == 'kernel':
        kernel.fn_local = dn_tmp + fn
      if bn == 'root':
        rootfs.fn_local = dn_tmp + fn
  file.close()
  if not kernel.fn_local:
    die('Kernel image not found in TAR!')
  with open(kernel.fn_local, "rb") as file:
    kernel.data = file.read()
  if not rootfs.fn_local:
    die('Rootfs image not found in TAR!')
  with open(rootfs.fn_local, "rb") as file:
    rootfs.data = file.read()
  # TODO: insert hsqs to UBI FS !!!
  '''

def parse_fit(data, offset = 0):
  if data[offset:offset+4] != FIT_MAGIC:
    die('FIT: Incorrect image (0)')
  hdrsize = ctypes.sizeof(fdt_header)
  fit_hdr = fdt_header.from_buffer_copy(data[offset:offset+hdrsize])
  fit_size = fit_hdr.totalsize
  if fit_size < 1*1024*1024:
    die('FIT: Incorrect image (1)')
  if kernel.data:
    die('FIT: Found second "kernel" section')
  kernel.data = data[offset:offset + fit_size]
  print('FIT size = 0x%X (%d KiB)' % (fit_size, fit_size // 1024))
  fit_dt = fdt.parse_dtb(kernel.data)
  #if fit_dt.root.nodes[0]._name != 'images':
  #  die('FIT: Incorrect image (4)')
  fit_name = fit_dt.get_property('description').value
  print('FIT name = "{}"'.format(fit_name))
  kernel.fit = True
  if 'OpenWrt FIT' in fit_name:
    kernel.ostype = 'openwrt'    
  if not kernel.ostype:
    die('FIT: Currently supported only OpenWrt FIT images!')
  '''
  fdt1 = dt.get_node('/images/fdt-1')
  print('FDT desc = "{}"'.format(fdt1.get_property('description').value))
  print('FDT type = "{}"'.format(fdt1.get_property('type').value))
  print('FDT arch = "{}"'.format(fdt1.get_property('arch').value))
  print('FDT compression = "{}"'.format(fdt1.get_property('compression').value))
  if fdt1.get_property('type').value != 'flat_dt':
    die('FIT: Incorrect image (6)')
  kernel.hdr.arch = fdt1.get_property('arch').value
  '''
  if kernel.ostype == 'openwrt':
    if len(data) - offset - fit_size < 1024:
      print("WARN: FIT: 001")
      return 1
    rootfs_offset = data.find(UBIv1_MAGIC, offset + fit_size)
    if rootfs_offset >= 0:
      rootfs_offset -= offset
      print('FIT: UBI offset = 0x%X' % rootfs_offset)
    else:
      rootfs_offset = data.find(HSQS_MAGIC, offset + fit_size)      
      if rootfs_offset >= 0:
        rootfs_offset -= offset
        print('FIT: HSQS offset = 0x%X' % rootfs_offset)
    if rootfs_offset < 0:
      if not rootfs.data:
        print("WARN: FIT: rootfs not found")
      return 1
    if rootfs.data:
      die('FIT: Found two RootFS images')
    rootfs.data = data[offset+rootfs_offset:]
    rootfs.addr = rootfs_offset
    return 2
  return 1

def parse_factory(data, offset = 0):
  if offset + 512 > len(data):
    return -1
  kernel_size = 0
  if data[offset:offset+4] == UIMAGE_MAGIC:  # uImage
    pos = offset + 0x0C
    kernel_size = int.from_bytes(data[pos:pos+4], byteorder='big')
    kernel_size += 0x40
    if kernel.data:
      die('Found second "kernel" section!')
    kernel.data = data[offset:offset+kernel_size]
    if kernel_size > len(kernel.data):
      die("Kernel header is incorrect!")
  if data[offset:offset+4] == FIT_MAGIC:  # factory squashfs image
    return parse_fit(data, offset)
  if kernel_size == 0:
    die("Kernel header is incorrect!")
  if kernel_size < 1*1024*1024:
    die('Kernel image size is too small! (size: {} KB)'.format(kernel_size // 1024))
  pos = 0x1C
  kernel.hdr.size = kernel_size
  kernel.hdr.arch = int.from_bytes(kernel.data[pos+1:pos+2], byteorder='little')
  kernel.hdr.type = int.from_bytes(kernel.data[pos+2:pos+3], byteorder='little')
  kernel.hdr.comp = int.from_bytes(kernel.data[pos+3:pos+4], byteorder='little')
  kernel.hdr.name = kernel.data[0x20:0x40]
  #print('cpu_arch = {}, img_type = {}, cmp_type = {}'.format(cpu_arch, img_type, cmp_type))
  if kernel.hdr.type != 2:      # IH_TYPE_KERNEL
    die('Kernel type is incorrect!')  
  if kernel.hdr.arch == 5 and dev.info.cpu_arch != 'mips':
    die('Kernel arch is not MIPS!')  
  if kernel.hdr.arch == 2 and dev.info.cpu_arch != 'armv7':
    die('Kernel arch is not ARMv7!')
  try:
    iname = kernel.hdr.name.decode('ascii')    
  except Exception:
    iname = None
  if kernel.hdr.name[0:1] == b'\x03':    # padavan kernel version
    iname = None
    if kernel.hdr.name[2:3] == b'\x03':  # padavan fw version
      ksize = int.from_bytes(kernel.hdr.name[0x1C:0x20], byteorder='big')
      if ksize > 1*1024*1024 and ksize < kernel_size:
        kernel.ostype = 'padavan'
        try:
          iname = kernel.hdr.name[4:0x1B].decode('ascii')    
        except Exception:
          iname = None
        kernel_size = ksize
        #kernel.hdr.size = kernel_size
        if kernel.data[ksize:ksize+4] != HSQS_MAGIC:
          die('Incorrect padavan kernel image! RootFS not found!')
        rootfs.data = kernel.data[ksize:]
  if iname is None:        
    die('Incorrect kernel image name! (name: "{}")'.format(kernel.hdr.name))
  print("Kernel image name = '{}'".format(iname))
  kernel.hdr.name = iname
  if kernel.ostype == 'padavan':
    return 2
  if c_stock:
    #if kernel.hdr.comp == 0:      # IH_COMP_NONE
    #  die("Stock Kernel must be packed!")
    if iname.find('OpenWrt') < 0 or iname.find('Linux-3.') < 0:
      die("Incorrect stock kernel image name!")
    kernel.ostype = 'stock'
  else:
    if iname.find('PandoraBox') >= 0:
      die('Pandora kernel not supported!')
    if iname.find('OpenWrt') >= 0:
      if iname.find('Linux-4.') > 0 or iname.find('Linux-5.') > 0 or iname.find('Linux-6.') > 0:
        kernel.ostype = 'openwrt'
  if not kernel.ostype:
    die("Incorrect kernel image! Can't detect target OS type!")
  if kernel_size > 4*1024*1024 - 128:
    die('Kernel image size is too large! (size: {} KB)'.format(kernel_size // 1024))
  if kernel_size > 0:
    offset += kernel_size
    data = data[offset:]
    print('kernel_size = 0x%X' % kernel_size)  
    if len(data) < 1024:
      return 1
    offlist = []  
    offlist.append(data.find(UBIv1_MAGIC))
    offlist.append(data.find(HSQS_MAGIC))
    if len(offlist) == 0:
      return 1
    rootfs_offset = 0xFFFFFFFF
    for i, off in enumerate(offlist):
      if off >= 0 and off < rootfs_offset:
        rootfs_offset = off
    if rootfs_offset == 0xFFFFFFFF:
      return 1
    if rootfs.data:
      die('Found two RootFS images!')
    rootfs.data = data[rootfs_offset:]
    return 2
  return 0

if c_factory:
  fname = [i for i in imglist if i.type == 'factory'][0].fn
  with open(fname, "rb") as file:
    data = file.read()
  ret = parse_factory(data)
  if ret < 1:
    die('Kernel section not found!')
  #print('c_factory OK')

if c_rootfs:
  if rootfs.data:
    die('Found two RootFS images!')
  fname = [i for i in imglist if i.type == 'rootfs'][0].fn
  with open(fname, "rb") as file:
    data = file.read()
  rootfs.data = data

if c_stock:
  fname = imglist[0].fn  
  with open(fname, "rb") as file:
    data = file.read()
  imglst = []
  for i in range(4):
    p = 0x10 + i * 4
    img = types.SimpleNamespace()
    img.offset = int.from_bytes(data[p:p+4], byteorder='little')
    if img.offset == 0:
      continue
    if img.offset > len(data) - 64:
      die('Incorrect stock image! (2)')
    if data[img.offset:img.offset+4] != b'\xBE\xBA\x00\x00':
      die('Incorrect stock image! (3)')
    img.size = int.from_bytes(data[img.offset+8:img.offset+8+4], byteorder='little')
    hdr_size = 0x30
    img.data = data[img.offset+hdr_size:img.offset+hdr_size+img.size]
    if len(img.data) != img.size:
      die('Incorrect stock image! (4)')
    #print('offset = {}  header = {}'.format("%08X" % (img.offset + hdr_size), img.data[:4]))
    imglst.append(img)
  if not imglst:
    die('Incorrect stock image! (5)')
  for i, img in enumerate(imglst):
    if len(img.data) < 1*1024*1024:  # skip uboot and other files
      continue
    if img.data[:4] == UIMAGE_MAGIC:
      if kernel.data:
        die('Incorrect stock image! (6)')
      kernel.data = img.data
    if img.data[:4] == UBI_MAGIC or img.data[:4] == HSQS_MAGIC:
      if rootfs.data:
        die('Incorrect stock image! (7)')
      rootfs.data = img.data
    if img.data[:4] == FDT_MAGIC:
      if kernel.data or rootfs.data:
        die('Incorrect stock image! (8)')
      ret = parse_factory(img.data)
      if ret < 1:
        die('Kernel section not found!')
  #print('c_stock OK')

  
if not kernel.data:
  die('Kernel data not found!')

kernel.fn_remote = '/tmp/kernel.bin'
kernel.fn_local = fn_kernel
with open(kernel.fn_local, "wb") as file:
  file.write(kernel.data)

if not rootfs.data:
  die('RootFS data not found!')

rootfs.fn_remote = '/tmp/rootfs.bin'
rootfs.fn_local = fn_rootfs
with open(rootfs.fn_local, "wb") as file:
  file.write(rootfs.data)


if kernel.data[:4] == FIT_MAGIC:
  part_fw = dev.get_part('firmware')
  part_fw1 = dev.get_part('firmware1')
  if part_fw and part_fw1:
    kernel.addr = part_fw['addr']
    if not rootfs.addr:
      die("FIT: Can't found addr for UBI image")
    kernel_size = len(kernel.data)
    kernel.data += (b"\x00" * (rootfs.addr - kernel_size)) + rootfs.data
    rootfs.addr = None
    kernel.fn_remote = '/tmp/fitubi.bin'
    kernel.fn_local = fn_fitubi
    with open(kernel.fn_local, "wb") as file:
      file.write(kernel.data)
    kernel.partname = "firmware"  # kernel0
  if not kernel.addr:
    die("FIT: Can't found partition for flashing FIT image")

if kernel.data[:4] == UIMAGE_MAGIC:
  data2 = kernel.data[0x40:]
  img_comp = kernel.hdr.comp
  if img_comp == 0:
    loader_data = data2[:0x8000]
    x1 = loader_data.find(b'Incorrect LZMA stream properties!') # b'OpenWrt kernel loader for MIPS based SoC'
    x2 = loader_data.find(b'XZ-compressed data is corrupt')
    if x1 < 0 and x2 < 0:
      die('Kernel image not compressed! It is very strange!')
    if x1 > 0 and x2 > 0:
      die("Strange error! (1)")
    if x1 > 0:
      k1 = loader_data.find(b'\x00\x6D\x00\x00', x1)   # LZMA prefix
      k2 = loader_data.find(b'\x00\x5D\x00\x00', x1)   # LZMA prefix
      if k1 < 0 and k2 < 0:
        die("Can't unpack kernel image! (lzma loader)")
      if k1 > 0 and k2 > 0:
        k = min(k1, k2)
      else: 
        k = k1 if k1 > 0 else k2      
      data2 = data2[k+1:]
      img_comp = 3  #  IH_COMP_LZMA
    if x2 > 0:
      k = loader_data.find(b'\xFD7zXZ\x00', x2)   # XZ prefix
      if k < 0:
        die("Can't unpack kernel image! (XZ loader)")
      data2 = data2[k:]
      img_comp = 5    # IH_COMP_XZ
  if img_comp == 3:   # IH_COMP_LZMA
    kernel.data2 = lzma.decompress(data2)
  if img_comp == 5:   # IH_COMP_XZ
    kernel.data2 = lzma.decompress(data2, lzma.FORMAT_XZ)
  if kernel.data2:
    with open(dn_tmp + 'kernel_unpacked.bin', "wb") as file:
      file.write(kernel.data2)
  if kernel.ostype == 'openwrt':
    dtb = get_dtb(kernel.data, 32)
    if not dtb and kernel.data2:
      dtb = get_dtb(kernel.data2, 0)
    if not dtb:
      die("Can't found FDT (flattened device tree)")
    kernel_part = get_dtb_part_info(dtb, "kernel")
    if not kernel_part:
      die('Cannot found "kernel" partition in DTB!')
    print('part kernel = 0x%X (size: 0x%X)' % (kernel_part['addr'], kernel_part['size']))
    kernel.addr = kernel_part['addr']
    part = dev.get_part_by_addr(kernel.addr)
    if not part:
      die("Can't support flashing kernel to addr 0x%X" % kernel.addr)      
    kernel2_part = get_dtb_part_info(dtb, "kernel_dup")
    if not kernel2_part:
      kernel2_part = get_dtb_part_info(dtb, "kernel_stock")
    if not kernel2_part:
      die('Cannot found "kernel_dup"/"kernel_stock" partition in DTB!')
    print('part kernel2 = 0x%X (size: 0x%X)' % (kernel2_part['addr'], kernel2_part['size']))
    kernel.addr2 = kernel2_part['addr']
    part = dev.get_part_by_addr(kernel.addr2)
    if not part:
      die("Can't support flashing kernel to addr 0x%X" % kernel.addr2)      
    ubi_part = get_dtb_part_info(dtb, "ubi")
    if not ubi_part:
      die('Cannot found "ubi" partition in DTB!')
    print('part ubi = 0x%X (size: 0x%X)' % (ubi_part['addr'], ubi_part['size']))
    rootfs.addr = ubi_part['addr']
    part = dev.get_part_by_addr(rootfs.addr)
    if not part:
      die("Can't support flashing ubi to addr 0x%X" % rootfs.addr)
    if len(rootfs.data) + 0x8000 >= part['size']:
      die("Partition '%s' is too small (data size: 0x%X, part size: 0x%X)" % (part['name'], len(rootfs.data), part['size']))
  if kernel.ostype == 'padavan':
    kernel.addr = 0x600000
    part = dev.get_part_by_addr(kernel.addr)
    if not part:
      die('Partition for addr {} not found'.format("0x%X" % kernel.addr))
    part_size = part['size']
    if len(kernel.data) > part_size:
      rootfs.data = kernel.data[part_size:]
      kernel.data = kernel.data[:part_size]
      rootfs.addr = kernel.addr + part_size
      part2 = dev.get_part_by_addr(rootfs.addr)
      if not part2:
        die('Partition for addr {} not found'.format("0x%X" % rootfs.addr))
    else:
      rootfs.addr = kernel.addr + part_size
      part2 = dev.get_part_by_addr(rootfs.addr)
      if not part2:
        die('Partition for addr {} not found'.format("0x%X" % rootfs.addr))
      part2_size = part2['size']
      if part2_size > 8*1024*1024:
        part2_size = 8*1024*1024
      rootfs.data = b'\x00' * part2_size


with open(kernel.fn_local, "wb") as file:
  file.write(kernel.data)

with open(rootfs.fn_local, "wb") as file:
  file.write(rootfs.data)

dev.get_bootloader()
if not dev.bl.img:
  die("Can't dump current bootloader!")

dev.get_env_list()
if not dev.env.fw.data or dev.env.fw.len <= 0:
  die("Can't dump current NVRAM params!")

fw_num = None
print(f"current flag_boot_rootfs = {dev.rootfs.num}")
rootfs1_num = dev.get_part_num("rootfs1")

if c_stock:
  if dev.rootfs.num is None or dev.rootfs.num < 0:
    die("Can't detect current booted rootfs!")
  fw_num = 1 - dev.rootfs.num
  #if dev.env.fw.var['flag_boot_rootfs'] == str(dev.rootfs.num):
  #  die("First, you should change the number of the boot kernel to {} !!!".format(fw_num))
  if rootfs1_num > 0:
    kernel.partname = "kernel{}".format(fw_num)
    kp = dev.get_part_num(kernel.partname)
    if kp <= 0:
      die("Partition {} not found!".format(kernel.partname))
    kernel.addr = dev.partlist[kp]['addr']
    kernel.cmd = 'mtd -e "{part}" write "{bin}" "{part}"'.format(part=kernel.partname, bin=kernel.fn_remote)
    rootfs.partname = "rootfs{}".format(fw_num)
    rp = dev.get_part_num(rootfs.partname)
    if rp <= 0:
      die("Partition {} not found!".format(rootfs.partname))
    rootfs.addr = dev.partlist[rp]['addr']
    rootfs.cmd = 'mtd -e "{part}" write "{bin}" "{part}"'.format(part=rootfs.partname, bin=rootfs.fn_remote)

if kernel.fit is True:
  if not kernel.addr or not kernel.partname:
    die('FIT: Unknown addr for flashing!')
  if c_stock:
    if fw_num == 1:
      kernel.partname = 'firmware1'
  else:  
    fw_num = 0   # target partition is 'firmware'
  kernel.cmd = 'mtd -e "{part}" write "{bin}" "{part}"'.format(part=kernel.partname, bin=kernel.fn_remote)
  rootfs.cmd = None

elif kernel.ostype == 'openwrt' or kernel.ostype == 'padavan':
  fw_addr = 0
  if not kernel.addr or not rootfs.addr:
    die('Unknown addr for flashing!')
  part = dev.get_part_by_addr(kernel.addr)
  if not part:
    die('Partition for addr {} not found'.format("0x%X" % kernel.addr))
  if part['size'] < len(kernel.data):
    die('Partition size is too small!')
  kernel.partname = part['name']
  kernel.cmd = 'mtd -e "{part}" write "{bin}" "{part}"'.format(part=kernel.partname, bin=kernel.fn_remote)
  if kernel.addr2:
    part = dev.get_part_by_addr(kernel.addr2)
    if not part:
      die('Partition for addr {} not found'.format("0x%X" % kernel.addr2))
    if part['size'] < len(kernel.data):
      die('Partition size is too small!')
    kernel.cmd += ' ; mtd -e "{part}" write "{bin}" "{part}"'.format(part=part['name'], bin=kernel.fn_remote)
  part = dev.get_part_by_addr(rootfs.addr)
  if not part:
    die('Partition for addr {} not found'.format("0x%X" % rootfs.addr))
  if part['size'] < len(rootfs.data):
    die('Partition size is too small!')
  rootfs.partname = part['name']
  rootfs.cmd = 'mtd -e "{part}" write "{bin}" "{part}"'.format(part=rootfs.partname, bin=rootfs.fn_remote)

if dev.bl.type == 'breed':
  fw_addr = None
  if 'autoboot.command' in dev.env.breed.var:
    cmd = dev.env.breed.var['autoboot.command']
    lst = cmd.split(' ')
    if len(lst) == 3:
      try:
        fw_addr = int(lst[2].strip(), 16)
      except Exception:
        fw_addr = None
  if fw_addr and fw_addr == kernel.addr:
    print("Breed boot address is correct! (addr: 0x%X)" % fw_addr)
  elif img_write:
    if fw_num is not None:
      fw_addr = activate_boot.breed_boot_change(gw, dev, fw_num, None, None)
    else: 
      fw_addr = activate_boot.breed_boot_change(gw, dev, None, kernel.addr, None)
    pass


if not kernel.cmd or (not kernel.fit and not rootfs.cmd):
  die("Flashing recipe unknown!")

gw.set_timeout(12)
gw.upload(kernel.fn_local, kernel.fn_remote)
if rootfs.cmd:
  gw.upload(rootfs.fn_local, rootfs.fn_remote)

cmd = []
cmd.append("nvram set bootdelay=3")
cmd.append("nvram set boot_wait=on")
cmd.append("nvram set bootmenu_delay=30")
cmd.append("nvram set ssh_en=1")
cmd.append("nvram set uart_en=1")
cmd.append("nvram commit")
gw.run_cmd(cmd, timeout = 8)

if fw_num is not None:
  print("Run scripts for change NVRAM params...")
  if img_write:
    activate_boot.uboot_boot_change(gw, fw_num)
  print('Boot from partition "{}" activated.'.format(kernel.partname))

print('Writing kernel image to addr {} ...'.format("0x%08X" % kernel.addr))
print("  " + kernel.cmd)
if img_write:
  gw.run_cmd(kernel.cmd, timeout = 34)

if rootfs.cmd:
  print('Writing rootfs image to addr {} ...'.format("0x%08X" % rootfs.addr))
  print("  " + rootfs.cmd)
  if img_write:
    gw.run_cmd(rootfs.cmd, timeout = 60)

if img_write:
  die('TEST is over')

print("The firmware has been successfully flashed!")

if kernel.fit:
  print('Send command "reboot" via SSH ...')
  try:
    gw.run_cmd("reboot -f")
  except ssh2.exceptions.SocketRecvError as e:
    pass
  print("Force REBOOT activated!")
else:
  gw.run_cmd("sync ; umount -a", timeout = 5)
  print("Please, reboot router!")

