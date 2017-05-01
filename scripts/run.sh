#!/bin/sh
cat /etc/passwd > /tmp/passwd
echo "$(id -u):x:$(id -u):$(id -g):dynamic uid:$SPARK_HOME:/bin/false" >> /tmp/passwd

export NSS_WRAPPER_PASSWD=/tmp/passwd
# NSS_WRAPPER_GROUP must be set for NSS_WRAPPER_PASSWD to be used
export NSS_WRAPPER_GROUP=/etc/group

export LD_PRELOAD=libnss_wrapper.so

export SPARK_MASTER=${VAR_SPARK_MASTER:-local[*]}
export WIKIEOD_FILE=/data/wikieod.parquet

pushd dist
export LC_ALL=en_US.utf8
export LANG=en_US.utf8
exec spark-submit --driver-memory=5G --master="${SPARK_MASTER}" ./app.py
popd
