#
import gc
try:
  import atticfan
except ImportError:
  print('Atticfan app is not installed')
else:
  gc.collect()
  atticfan.main()
