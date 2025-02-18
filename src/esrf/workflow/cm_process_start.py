import os
import sys
import glob
import time
import celery

if "v3_dev" in __file__:
    sys.path.insert(0, "/opt/pxsoft/scipion/v3_dev/ubuntu20.04/scipion-em-esrf")

#
import motioncorr.constants

from esrf.utils.esrf_utils_path import UtilsPath
from esrf.utils.esrf_utils_ispyb import UtilsISPyB
from esrf.workflow.command_line_parser import getCommandlineOptions
from esrf.workflow.cm_process_worker import run_workflow_commandline

# print(getCommandlineOptions.__module__)
config_dict = getCommandlineOptions()
# config_dict = {
#     "dataDirectory": "/data/visitor/mx2112/cm01/20220426/RAW_DATA/igm-grid3-2",
#     "filesPattern": "Images-Disc1/GridSquare_*/Data/FoilHole_*_fractions.tiff",
#     "scipionProjectName": "test_celery_{0}".format(time.time()),
#     "proteinAcronym": "igm-fccore",
#     "sampleAcronym": "g3",
#     "doseInitial": 0.0,
#     "magnification": 105000,
#     "imagesCount": 40,
#     "voltage": 300000,
#     "dosePerFrame": 0.935,
#     "dataStreaming": True,
#     "alignFrame0": 1,
#     "alignFrameN": 0,
#     "phasePlateData": False,
#     "no2dClass": True,
#     "onlyISPyB": False,
#     "noISPyB": True,
#     "particleElimination": False,
#     "samplingRate": 0.84,
#     "superResolution": True,
#     "partSize": 300.0,
#     "defectMapPath": None,
#     "gainFilePath": None,
#     "secondGrid": False,
#     "proposal": "mx2112",
#     "celery_worker": "cmproc3"
# }
# Command line: --dataDirectory /data/visitor/mx2112/cm01/20220426/RAW_DATA/igm-grid3-2
# --dosePerFrame 0.935 --samplingRate 0.84
#
#
if config_dict["filesPattern"] is None:
    # No filesPattern, let's assume that we are dealing with EPU data
    config_dict[
        "filesPattern"
    ] = "Images-Disc*/GridSquare_*/Data/FoilHole_*_fractions.tiff"

# Check how many movies are present on disk
listMovies = glob.glob(
    os.path.join(config_dict["dataDirectory"], config_dict["filesPattern"])
)
noMovies = len(listMovies)
# Check how many movies are present on disk
listMovies = glob.glob(
    os.path.join(config_dict["dataDirectory"], config_dict["filesPattern"])
)
noMovies = len(listMovies)
if config_dict["secondGrid"] or config_dict["thirdGrid"]:
    if config_dict["secondGrid"] and config_dict["thirdGrid"]:
        raise RuntimeError("--secondGrid and --thirdGrid cannot be used at the same time!")
    if noMovies > 0:
        raise RuntimeError("--secondGrid or --thirdGrid used and images already exists on disk!")
    # Check that we have voltage, imagesCount and magnification:
    for key in ["voltage", "magnification", "imagesCount"]:
        if key not in config_dict or config_dict[key] is None:
            raise RuntimeError(
                "--secondGrid or --thirdGrid used, missing command line argument '--{0}'!".format(key)
            )
    # Assume EPU TIFF data
    config_dict["dataType"] = 1  # "EPU_TIFF"
    config_dict["gainFlip"] = motioncorr.constants.FLIP_LEFTRIGHT
    config_dict["gainRot"] = motioncorr.constants.ROTATE_180
    config_dict[
        "filesPattern"
    ] = "Images-Disc*/GridSquare_*/Data/FoilHole_*_fractions.tiff"
elif noMovies == 0:
    print(
        "ERROR! No movies available in directory {0} with the filesPattern {1}.".format(
            config_dict["dataDirectory"], config_dict["filesPattern"]
        )
    )
    sys.exit(1)
else:
    print("Number of movies available on disk: {0}".format(noMovies))
    firstMovieFullPath = listMovies[0]
    print("First movie full path file: {0}".format(firstMovieFullPath))


if noMovies == 0:
    if config_dict["secondGrid"] or config_dict["thirdGrid"]:
        print("Will wait for images from second or third grid.")
        firstMovieFullPath = None
    else:
        print("ERROR - no files in direcory {0}".format(config_dict["dataDirectory"]))
        print("found with the pattern '{0}'".format(config_dict["filesPattern"]))
        sys.exit(1)
else:
    firstMovieFullPath = listMovies[0]

jpeg = None
xml = None
mrc = None

if not config_dict["secondGrid"] and not config_dict["thirdGrid"]:
    if firstMovieFullPath.endswith("tiff"):
        print("********** EPU tiff data **********")
        config_dict["dataType"] = 1  # "EPU_TIFF"
        config_dict["gainFlip"] = motioncorr.constants.FLIP_LEFTRIGHT
        config_dict["gainRot"] = motioncorr.constants.ROTATE_180

        jpeg, mrc, xml, gridSquareThumbNail = UtilsPath.getEpuTiffMovieJpegMrcXml(
            firstMovieFullPath
        )
        if xml is None:
            print("*" * 80)
            print("*" * 80)
            print("*" * 80)
            print(
                "Error! Cannot find metadata files in the directory which contains the following movie:"
            )
            print(firstMovieFullPath)
            print("*" * 80)
            print("*" * 80)
            print("*" * 80)
            sys.exit(1)
        dictResults = UtilsPath.getXmlMetaData(xml)
        config_dict["doPhaseShiftEstimation"] = dictResults["phasePlateUsed"]
        config_dict["magnification"] = int(dictResults["magnification"])
        config_dict["voltage"] = int(dictResults["accelerationVoltage"])
        config_dict["imagesCount"] = int(dictResults["numberOffractions"])
    elif firstMovieFullPath.endswith("eer"):
        print("********** EER data **********")
        config_dict["dataType"] = 3  # "EER"

proposal = UtilsISPyB.getProposal(config_dict["dataDirectory"])

if config_dict["noISPyB"]:
    print("No upload to ISPyB or iCAT")
    db = -1
elif proposal is None:
    # Check proposal
    # db=0: production
    # db=1: valid
    # db=2: linsvensson
    print(
        "WARNING! No valid proposal could be found for directory {0}.".format(
            config_dict["dataDirectory"]
        )
    )
    print("")
    answer = input("Would you like to enter a valid proposal name now (yes/no)? ")
    while answer != "yes" and answer != "no":
        print("")
        answer = input(
            "Please answer 'yes' or 'no'. Would you like to enter a valid proposal name now? "
        )
    if answer == "yes":
        proposal = input("Please enter a valid proposal name: ")
        code, number = UtilsISPyB.splitProposalInCodeAndNumber(proposal)
        while code is None:
            print("'{0}' is not a valid proposal name.".format(proposal))
            print("")
            proposal = input(
                "Please enter a valid proposal name (mxXXXX, ih-lsXXXX etc): "
            )
            code, number = UtilsISPyB.splitProposalInCodeAndNumber(proposal)
    else:
        proposal = None

if proposal is None:
    print("WARNING! No data will be uploaded to ISPyB.")
    db = 3
else:
    if proposal == "mx415":
        # Use valid data base
        print("ISPyB valid data base used")
        db = 1
    elif proposal == "mx2112":
        # Use valid data base
        print("ISPyB production data base used")
        db = 0
    else:
        # Use productiond data base
        print("ISPyB production data base used")
        db = 0

config_dict["proposal"] = proposal
config_dict["db"] = db

date_string = time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time()))
config_dict["scipionProjectName"] = "{0}_{1}_{2}_{3}".format(
    config_dict["proposal"],
    config_dict["proteinAcronym"],
    config_dict["sampleAcronym"],
    date_string
)

user_name = os.getlogin()

log_dir = os.path.join("/tmp_14_days", user_name, "scipion_logs")
if not os.path.exists(log_dir):
    os.makedirs(log_dir, mode=0o777)
log_file_name = "{0}.log".format(config_dict["scipionProjectName"])
config_dict["log_path"] = os.path.join(log_dir, log_file_name)

if config_dict["celery_worker"] == "None":
    run_workflow_commandline(config_dict)
else:

    celery_worker = config_dict["celery_worker"]

    active_workers = celery.current_app.control.inspect().active()

    worker_name = "celery.{0}@{1}".format(user_name, celery_worker)
    print("Worker name: '{0}'".format(worker_name))
    found_worker = False
    if active_workers is None:
        print("No active workers!!!")
        sys.exit(1)
    else:
        for worker_key, worker_value in active_workers.items():
            print(worker_key)
            if worker_key == worker_name:
                # Check that worker is idle
                if len(worker_value) == 0:
                    found_worker = True
                    break
                elif config_dict["secondGrid"]:
                    print("Scheduling second grid job.")
                    found_worker = True
                    break
                elif config_dict["thirdGrid"]:
                    print("Scheduling second grid job.")
                    found_worker = True
                    break
                else:
                    found_worker = True
                    break

    if found_worker:
        print("Launching processing on worker '{0}' with the following parameters:".format(worker_name))
        for arg_key, arg_value in config_dict.items():
            print("{0:25s}= {1}".format(arg_key, arg_value))
        app = celery.Celery()
        app.config_from_object("esrf.workflow.celeryconfig")
        future = app.send_task(
            "esrf.workflow.cm_process_worker.run_workflow",
            args=(config_dict,),
            queue=worker_name,
        )
        task_id = future.task_id
        # Check that the job actually started
        print("Processing started, please wait for check if running...")
        time.sleep(5)
        active_workers = celery.current_app.control.inspect().active()
        is_running = False
        for worker_key, worker_value in active_workers.items():
            if worker_name == worker_key and len(worker_value) > 0:
                is_running = True
        if is_running:
            print("Worker started, celery task_id = {0}".format(task_id))
        else:
            print("Error! Worker didn't start! Please check log files in this directory:")
            print(log_dir)
            sys.exit(1)
    else:
        print("No worker found!")
