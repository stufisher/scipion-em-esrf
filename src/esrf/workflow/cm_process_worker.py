import gc
import os
import sys
import json
import glob
import time
import pprint
import celery
import socket
import logging
import objgraph
import tempfile
import datetime
import traceback

import pyworkflow
import motioncorr.constants
from esrf.workflow.workflow import preprocessWorkflow

from esrf.utils.esrf_utils_path import UtilsPath

user_name = os.getlogin()
host_name = socket.gethostname()
queue_name = "celery." + user_name + "@" + host_name

# logger = logging.getLogger("cm_process_worker")
# streamHandler = logging.StreamHandler()
# logFileFormat = "%(asctime)s %(levelname)-8s %(message)s"
# formatter = logging.Formatter(logFileFormat)
# streamHandler.setFormatter(formatter)
# streamHandler.setLevel(logging.INFO)
# logger.addHandler(streamHandler)
# logger.setLevel(logging.INFO)
# logger.debug("1"*80)
# logger.info("2"*80)
# logger.warning("3"*80)
# logger.error("4"*80)
# import pprint
# pprint.pprint(app.control.inspect().stats())

app = celery.Celery()
app.config_from_object("esrf.workflow.celeryconfig")

def init_logging(config_dict):
    # Set up logging
    logger = logging.getLogger("cm_process_worker")
    # Set up stream handler
    stream_handler = logging.StreamHandler()
    log_file_format = "%(asctime)s %(levelname)-8s %(message)s"
    formatter = logging.Formatter(log_file_format)
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.INFO)
    logger.addHandler(stream_handler)
    # Set up log file handler
    log_path = config_dict["log_path"]
    info_hdlr = logging.FileHandler(log_path)
    info_formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    info_hdlr.setFormatter(info_formatter)
    info_hdlr.setLevel(logging.DEBUG)
    logger.addHandler(info_hdlr)
    logger.setLevel(logging.DEBUG)
    return logger


def check_active_workers(config_dict):
    # Check if there are worker(s) already running on this computer
    logger = logging.getLogger("cm_process_worker")
    logger.info("Checking active workers.")
    active_workers = celery.current_app.control.inspect().active()
    if active_workers is not None and config_dict["celery_worker"] is None:
        for key, value in active_workers.items():
            logger.debug("Worker: {0} {1}".format(key, value))
            if host_name in key:
                logger.warning("A Scipion Celery worker is already running on this computer!")
    else:
        logger.info("No active workers detected.")


@app.task()
def revoke_tst(input_data):
    logger = logging.getLogger("cm_process_worker")
    logger.info("In test_revoke")
    try:
        while True:
            time.sleep(5)
            logger.info("Sleeping...")
    except:
        logger.info("In Except")
    finally:
        logger.info("In finally")


def getUpdatedProtocol(protocol):
    """Retrieve the updated protocol and close db connections"""
    logger = logging.getLogger("cm_process_worker")
    prot2 = None
    try:
        prot2 = pyworkflow.protocol.getProtocolFromDb(
            os.getcwd(), protocol.getDbPath(), protocol.getObjId()
        )
        # Close DB connections
        prot2.getProject().closeMapper()
        prot2.closeMappers()
    except Exception as e:
        logger.info("ERROR! Exception caught: {0}".format(e))
    return prot2


# https://www.programcreek.com/python/?CodeExample=get+num+gpus
def get_num_gpus():
    """Returns the number of GPUs available"""
    from pycuda import driver

    driver.init()
    num_gpus = driver.Device.count()
    return num_gpus


def set_location(config_dict):
    logger = logging.getLogger("cm_process_worker")
    if "dataDirectory" in config_dict and "RAW_DATA" in config_dict["dataDirectory"]:
        location = config_dict["dataDirectory"].replace("RAW_DATA", "PROCESSED_DATA")
    else:
        location = tempfile.mkdtemp(prefix="ScipionUserData_")
    if not os.path.exists(os.path.dirname(location)):
        os.makedirs(os.path.dirname(location), mode=0o755)
    logger.info("Scipion project location: {0}".format(location))
    config_dict["location"] = location
    # All param json file
    config_dict["allParamsJsonFile"] = os.path.join(location, "allParams.json")
    logger.info(
        "Location of allParams file: {0}".format(config_dict["allParamsJsonFile"])
    )
    if os.path.exists(config_dict["allParamsJsonFile"]):
        logger.info("Using existing allParams file")
    return location


def set_ispyb_database(config_dict):
    logger = logging.getLogger("cm_process_worker")
    if config_dict["proposal"] is None:
        logger.info("WARNING! No data will be uploaded to ISPyB.")
        config_dict["noISPyB"] = True
        config_dict["db"] = -1
        config_dict["proposal"] = "Unknown"
    elif config_dict["noISPyB"]:
        logger.info("No upload to ISPyB or iCAT")
        config_dict["db"] = -1
    else:
        if config_dict["proposal"] == "mx415":
            # Use valid data base
            logger.info("ISPyB valid data base used")
            config_dict["db"] = 1
        elif config_dict["proposal"] == "mx2112":
            # Use production data base
            logger.info("ISPyB production data base used")
            config_dict["db"] = 0
        else:
            # Use productiond data base
            logger.info("ISPyB production data base used")
            config_dict["db"] = 0


def create_blackfile_list(config_dict):
    logger = logging.getLogger("cm_process_worker")
    config_dict["blacklistFile"] = None
    if os.path.exists(config_dict["allParamsJsonFile"]):
        # Check how many movies are present on disk
        list_movies = glob.glob(
            os.path.join(config_dict["dataDirectory"], config_dict["filesPattern"])
        )
        black_list = UtilsPath.getBlacklistAllMovies(
            list_movies, config_dict["allParamsJsonFile"]
        )
        blacklist_file = os.path.join(config_dict["location"], "blacklist.txt")
        with open(blacklist_file, "w") as f:
            for filePath in black_list:
                f.write(filePath + "\n")
        config_dict["blacklistFile"] = blacklist_file
        logger.info("Black list file : " + config_dict["blacklistFile"])


def set_em_data(config_dict):
    if config_dict["phasePlateData"]:
        config_dict["sphericalAberration"] = 2.7
        config_dict["minDefocus"] = 2000
        config_dict["maxDefocus"] = 20000
        config_dict["astigmatism"] = 500.0
        config_dict["convsize"] = 85
        config_dict["doPhShEst"] = True
        config_dict["phaseShiftL"] = 0.0
        config_dict["phaseShiftH"] = 180.0
        config_dict["phaseShiftS"] = 5.0
        config_dict["phaseShiftT"] = 1
        config_dict["lowRes"] = 15.0
        config_dict["highRes"] = 4.0
    else:
        config_dict["sphericalAberration"] = 2.7
        config_dict["minDefocus"] = 5000
        config_dict["maxDefocus"] = 90000
        config_dict["astigmatism"] = 1000.0
        config_dict["convsize"] = 85
        config_dict["doPhShEst"] = False
        config_dict["phaseShiftL"] = 0.0
        config_dict["phaseShiftH"] = 180.0
        config_dict["phaseShiftS"] = 10.0
        config_dict["phaseShiftT"] = 0
        config_dict["lowRes"] = 30.0
        config_dict["highRes"] = 4.0
    if config_dict["lowRes"] > 50:
        config_dict["lowRes"] = 50
    if config_dict["superResolution"]:
        config_dict["binFactor"] = 2.0
    else:
        config_dict["binFactor"] = 1.0
    config_dict["extraParams2"] = ""
    config_dict["sampling2D"] = 3.0
    config_dict["gainFlip"] = motioncorr.constants.NO_FLIP
    config_dict["gainRot"] = motioncorr.constants.NO_ROTATION


def set_gpu_data(config_dict):
    num_gpus = get_num_gpus()
    if num_gpus == 0:
        raise RuntimeError("No GPUs found on this computer!")
    elif num_gpus == 1:
        config_dict["motioncor2Gpu"] = "0"
        config_dict["motioncor2Cpu"] = 2
    elif num_gpus in [2, 3]:
        config_dict["motioncor2Gpu"] = "0 1"
        config_dict["motioncor2Cpu"] = 3
    else:
        if config_dict["secondGrid"]:
            config_dict["motioncor2Gpu"] = "4 5 6 7"
        else:
            config_dict["motioncor2Gpu"] = "0 1 2 3"
        config_dict["motioncor2Cpu"] = 5
    config_dict["gctfGpu"] = "0"
    config_dict["gl2dGpu"] = "0"
    config_dict["cryoloGpu"] = "1"
    config_dict["relionGpu"] = "0,1"
    config_dict["numCpus"] = 48


def print_config(config_dict):
    logger = logging.getLogger("cm_process_worker")
    logger.info("")
    logger.info("Parameters:")
    logger.info("")
    logger.info("{0:30s}{1:>8s}".format("proposal", config_dict["proposal"]))
    logger.info("{0:30s}{1:8s}".format("dataDirectory", config_dict["dataDirectory"]))
    logger.info("{0:30s}{1:>8s}".format("filesPattern", config_dict["filesPattern"]))
    logger.info(
        "{0:30s}{1:>8s}".format("proteinAcronym", config_dict["proteinAcronym"])
    )
    logger.info("{0:30s}{1:>8s}".format("sampleAcronym", config_dict["sampleAcronym"]))
    logger.info("{0:30s}{1:8.0f}".format("voltage", config_dict["voltage"]))
    logger.info("{0:30s}{1:8d}".format("imagesCount", config_dict["imagesCount"]))
    logger.info("{0:30s}{1:8.2f}".format("doseInitial", config_dict["doseInitial"]))
    logger.info("{0:30s}{1:8.2f}".format("dosePerFrame", config_dict["dosePerFrame"]))
    logger.info(
        "{0:30s}{1:8.1f}".format(
            "sphericalAberration", config_dict["sphericalAberration"]
        )
    )
    logger.info("{0:30s}{1:8.1f}".format("gainFlip", config_dict["gainFlip"]))
    logger.info("{0:30s}{1:8.1f}".format("gainRot", config_dict["gainRot"]))
    logger.info("{0:30s}{1:8.2f}".format("minDefocus", config_dict["minDefocus"]))
    logger.info("{0:30s}{1:8.2f}".format("maxDefocus", config_dict["maxDefocus"]))
    logger.info("{0:30s}{1:8.1f}".format("astigmatism", config_dict["astigmatism"]))
    logger.info("{0:30s}{1:8d}".format("convsize", config_dict["convsize"]))
    logger.info("{0:30s}{1:>8}".format("doPhShEst", config_dict["doPhShEst"]))
    logger.info("{0:30s}{1:8.1f}".format("phaseShiftL", config_dict["phaseShiftL"]))
    logger.info("{0:30s}{1:8.1f}".format("phaseShiftH", config_dict["phaseShiftH"]))
    logger.info("{0:30s}{1:8.1f}".format("phaseShiftS", config_dict["phaseShiftS"]))
    logger.info("{0:30s}{1:8.1f}".format("phaseShiftT", config_dict["phaseShiftT"]))
    logger.info("{0:30s}{1:8.3f}".format("lowRes", config_dict["lowRes"]))
    logger.info("{0:30s}{1:8.3f}".format("highRes", config_dict["highRes"]))
    logger.info("{0:30s}{1:8.0f}".format("magnification", config_dict["magnification"]))
    logger.info("{0:30s}{1:8.2f}".format("samplingRate", config_dict["samplingRate"]))
    logger.info("{0:30s}{1:8.2f}".format("sampling2D", config_dict["sampling2D"]))
    logger.info("{0:30s}{1:8.2f}".format("partSize", config_dict["partSize"]))
    logger.info("{0:30s}{1:8.1f}".format("binFactor", config_dict["binFactor"]))
    logger.info("{0:30s}{1:>8}".format("dataStreaming", config_dict["dataStreaming"]))
    logger.info("{0:30s}{1:>8s}".format("motioncor2Gpu", config_dict["motioncor2Gpu"]))
    logger.info("{0:30s}{1:>8d}".format("motioncor2Cpu", config_dict["motioncor2Cpu"]))
    logger.info("{0:30s}{1:>8s}".format("gctfGpu", config_dict["gctfGpu"]))
    logger.info("{0:30s}{1:>8s}".format("gl2dGpu", config_dict["gl2dGpu"]))
    logger.info("{0:30s}{1:8d}".format("numCpus", config_dict["numCpus"]))
    logger.info("")
    logger.info("Scipion project name: {0}".format(config_dict["scipionProjectName"]))
    logger.info("Scipion user data location: {0}".format(config_dict["location"]))
    logger.info("All param json file: {0}".format(config_dict["allParamsJsonFile"]))
    logger.info("")


def update_all_params(config_dict):
    if os.path.exists(config_dict["allParamsJsonFile"]):
        with open(config_dict["allParamsJsonFile"]) as fd:
            all_params = json.loads(fd.read())
    else:
        all_params = {}
    key = "config_dict_" + time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time()))
    all_params[key] = config_dict
    os.makedirs(
        os.path.dirname(config_dict["allParamsJsonFile"]), exist_ok=True, mode=0o755
    )
    with open(config_dict["allParamsJsonFile"], "w") as fd:
        fd.write(json.dumps(all_params, indent=4))


@app.task()
def run_workflow(config_dict):
    logger = init_logging(config_dict)
    try:
        run_workflow_main(config_dict, logger)
    except BaseException:
        pass
        # (exc_type, exc_value, exc_traceback) = sys.exc_info()
        # errorMessage = "{0} {1}".format(exc_type, exc_value)
        # listTrace = traceback.extract_tb(exc_traceback)
        # for listLine in listTrace:
        #     errorMessage += '  File "%s", line %d, in %s%s' % (
        #         listLine[0],
        #         listLine[1],
        #         listLine[2],
        #         os.linesep,
        #     )
        # logger.error(errorMessage)
    logger.debug("Before gc")
    while gc.collect():
        pass
    logger.debug("After gc")
    has_found_project = False
    # for object in gc.get_objects():
    #     if isinstance(object, pyworkflow.project.Project):
    #         logger.warning("Project found!")
            # has_found_project = True
            # log_dir = os.path.dirname(config_dict["log_path"])
            # scipion_project_name = config_dict["scipionProjectName"]
            # graph_path = os.path.join(log_dir, scipion_project_name + ".png")
            # objgraph.show_backrefs([object], filename=graph_path, max_depth=10)
    # if not has_found_project:
    #     logger.debug("Project not found!")
    time.sleep(2)

def run_workflow_commandline(config_dict):
    logger = init_logging(config_dict)
    run_workflow_main(config_dict, logger)


def run_workflow_main(config_dict, logger):
    logger.info("Starting new workflow.")
    logger.debug(pprint.pformat(config_dict))
    # First check that a worker is not running on this computer
    check_active_workers(config_dict)
    # Set up location
    set_location(config_dict)
    # Set up ispyb data base
    set_ispyb_database(config_dict)
    # Create blacklist file with all movies already imported and motion corrected
    create_blackfile_list(config_dict)
    # Set diverse EM parameters
    set_em_data(config_dict)
    # Set GPU data
    set_gpu_data(config_dict)
    # Print configuration
    print_config(config_dict)
    # Update the allParamsJsonFile with the config_dict
    update_all_params(config_dict)

    # the project may be a soft link which may be unavailable to the cluster
    # so get the real path
    # manager = pyworkflow.project.manager.Manager()
    # try:
    #     project_path = os.readlink(
    #         manager.getProjectPath(config_dict["scipionProjectName"])
    #     )
    # except Exception:
    #     project_path = manager.getProjectPath(config_dict["scipionProjectName"])

    preprocessWorkflow(config_dict)

    # Start the project
    project = pyworkflow.project.Project(
        pyworkflow.Config.getDomain(),
        path=os.path.join(config_dict["location"], config_dict["scipionProjectName"]),
    )
    project.load()
    try:
        runs = project.getRuns()

        # Now assuming that there is no dependencies between runs
        # and the graph is lineal
        # for prot in runs:
        #     project.scheduleProtocol(prot)
        for prot in runs:
            prot_class_name = prot.getClassName()
            prot_label_name = prot.getObjLabel()
            if (
                prot_class_name not in sys.argv[3:]
                and prot_label_name not in sys.argv[3:]
            ):
                project.scheduleProtocol(prot)
            else:
                logger.info(
                    "\nNot scheduling '%s' protocol named '%s'.\n"
                    % (prot_class_name, prot_label_name)
                )

        # Monitor the execution:
        do_continue = True
        while do_continue:
            do_continue = False
            try:
                updated_runs = [getUpdatedProtocol(p) for p in runs]
                logger.info("")
                logger.info(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                for prot in updated_runs:
                    if prot is not None:
                        logger.info(
                            "{0} status: {1}".format(
                                prot.getRunName(), prot.getStatusMessage()
                            )
                        )
                        if prot.isActive():
                            do_continue = True
            except Exception as e:
                logger.info("ERROR! Exception caught: {0}".format(e))
                logger.info("Trying to continue anyway...")
                do_continue = True
            time.sleep(10)
    except Exception as e:
        logger.info("In except")
        logger.info(e)
    finally:
        logger.info("In finally")
        logger.info("Killing all scipion processes")
        runs = project.getRuns()
        for prot in runs:
            try:
                logger.info("Trying to stop protocol '{0}'".format(prot))
                project.stopProtocol(prot)
            except Exception:
                logger.info("Couldn't stop protocol {0}".format(prot))


@app.task()
def kill_workflow(config_dict):
    logger = init_logging(config_dict)
    logger.warning("Killing remaining processes")
    data_directory = config_dict["dataDirectory"]
    scipion_project_name = config_dict["scipionProjectName"]
    processed_data_directory = data_directory.replace("RAW_DATA", "PROCESSED_DATA")
    scipion_project_path = os.path.join(processed_data_directory, scipion_project_name)
    out = os.popen(f"pgrep -f {scipion_project_path}").read().strip()
    processes = list(map(int, out.splitlines()))
    for process in processes:
        logger.warning(f"Killing process {process}")
        os.kill(process, 9)


# if __name__ == "__main__":
#     argv = [
#         "worker",
#         "-l",
#         "INFO",
#         "-E",
#         "--concurrency=3",
#         "-Q",
#         queue_name,
#         "-n",
#         queue_name,
#         "--logfile",
#         log_path,
#         "--loglevel",
#         "INFO",
#         # "--detach"
#     ]
#     logger.info("Starting worker with the commands:")
#     logger.info(pprint.pformat(argv))
#     app.worker_main(argv=argv)
