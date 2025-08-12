import os

# Set the paths
fuzzer_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

dynamorio_path = os.path.join(fuzzer_path, "thirdparty", "deps", "dynamorio/build/bin64/drrun")
iftracer_path = os.path.join(fuzzer_path, "thirdparty", "deps", "iftracer/iftracer/libiftracer.so")
iflinetracer_path = os.path.join(fuzzer_path, "thirdparty", "deps", "iftracer/ifLineTracer/libifLineTracer.so")
libcbr_path = os.path.join(fuzzer_path, "thirdparty", "deps", "dynamorio/build/api/bin/libcbr.so")
