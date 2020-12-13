#
try:
  import atticfan
except ImportError:
  print('The atticfan app is not installed')
else:
  print('Running the AtticFan app')
  atticfan.main()
