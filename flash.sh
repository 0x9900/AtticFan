#!/bin/bash
#
set -xe

delay() {
    sleep 1
}

#delay && /opt/local/bin/ampy -d 1 mkdir lib
#delay && /opt/local/bin/ampy -d 1 mkdir html

#mpy-cross lib/logging.py
#delay && /opt/local/bin/ampy -d 1 put lib/logging.mpy lib/logging.mpy

#mpy-cross lib/bme280.py
#delay && /opt/local/bin/ampy -d 1 put lib/bme280.mpy lib/bme280.mpy

#mpy-cross -v wificonfig.py
#delay && /opt/local/bin/ampy -d 1 put wificonfig.mpy

#delay && /opt/local/bin/ampy -d 1 put main.py

~/bin/cssmin -f html/style.css
delay && /opt/local/bin/ampy -d 1 put html/style.min.css html/style.min.css
delay && /opt/local/bin/ampy -d 1 put html/index.html html/index.html

mpy-cross -v atticfan.py
delay && /opt/local/bin/ampy -d 1 put atticfan.mpy

delay && /opt/local/bin/ampy -d 1 ls
