#
try:
  import atticfan
except ImportError:
  print('Atticfan app is not installed')
else:
  print()
  atticfan.main()
