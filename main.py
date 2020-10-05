import time
try:
  import atticfan
except ImportError:
  print('The atticfan app is not installed')
else:
  time.sleep(10)
  atticfan.main()
