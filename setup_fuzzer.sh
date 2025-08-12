#!/bin/bash

mkdir -p thirdparty
mkdir -p thirdparty/pypackages/lib/python2.7/site-packages
mkdir -p thirdparty/deps
pypac=${PWD}/thirdparty/pypackages
iftracer=${PWD}/fuzzer/iftracer.zip
DYNAMORIO_CMAKE_DIR=${PWD}/thirdparty/deps/dynamorio/build/cmake

pushd thirdparty
  wget -nv https://github.com/numpy/numpy/releases/download/v1.16.6/numpy-1.16.6.zip \
    && unzip numpy-1.16.6.zip \
    && rm numpy-1.16.6.zip \
    && mv numpy-1.16.6 numpy \
    && cd numpy \
    && python setup.py install --prefix=${pypac}
  python -m pip install --target=${pypac} pyelftools==0.29 futures
popd

pushd thirdparty/deps
  DYNAMORIO_VERSION=cronbuild-9.0.19216
  git clone https://github.com/DynamoRIO/dynamorio.git
  pushd dynamorio
    git checkout ${DYNAMORIO_VERSION}
    mkdir build
    cd build
    cmake ..
    make -j$(nproc)
    make install
  popd

  cp ${iftracer} .
  unzip iftracer.zip
  rm iftracer.zip
  pushd iftracer/iftracer
    cmake -DDynamoRIO_DIR=${DYNAMORIO_CMAKE_DIR} CMakeLists.txt
    make -j$(nproc)
  popd
  pushd iftracer/ifLineTracer
    cmake -DDynamoRIO_DIR=${DYNAMORIO_CMAKE_DIR} CMakeLists.txt
    make -j$(nproc)
  popd
  wget -nv https://sourceware.org/pub/valgrind/valgrind-3.15.0.tar.bz2
  tar xjf valgrind-3.15.0.tar.bz2
  mv valgrind-3.15.0 valgrind
  pushd valgrind
    ./configure
    make -j$(nproc)
  popd
popd