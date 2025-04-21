"""
This module acts as the core implementation of the Experiment Runner. This module is executed as a script and
executes an MPC experiment on a given experiment host and orchestrates the MPC experiment.
"""

import re
import sys
import warnings
import logging
import traceback
from datetime import datetime
import python_utils.config_def as config_def
import python_utils.runner_defs as runner_defs
import python_utils.output_capture as out_cap
import python_utils.cleaner as clr
import python_utils.format_config as format_config
import click
import os
import shutil
import string
import random
import time
import subprocess
from python_utils import rendezvouz
from python_utils.consistency_cerebro import compile_cerebro_with_args, run_cerebro_with_args, compile_sha3_with_args, run_sha3_with_args

DEFAULT_CONFIG_NAME = "config.json"
DEFAULT_RESULT_FOLDER = "results/"

# Configure logging
def setup_logging(player_number, result_dir):
    """Set up logging to file and console."""
    log_file = os.path.join(result_dir, DEFAULT_RESULT_FOLDER, f"experiment_runner_{player_number}.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    logger = logging.getLogger('ExperimentRunner')
    logger.setLevel(logging.DEBUG)

    # File handler
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # Formatter
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger

def generate_random_prefix() -> str:
    """Generates a random output prefix for use with the MPC protocol VMs."""
    prefix = ''.join([random.choice(string.ascii_lowercase + string.digits) for _ in range(20)])
    logging.getLogger('ExperimentRunner').debug(f"Generated random prefix: {prefix}")
    return prefix

def prepare_config(player_number, result_dir):
    """Executes the config model construction step."""
    logger = logging.getLogger('ExperimentRunner')
    logger.info(f"Preparing configuration for player {player_number}")

    json_config_path = os.path.join(result_dir, DEFAULT_CONFIG_NAME)
    logger.debug(f"JSON config path: {json_config_path}")

    try:
        json_config_obj = config_def.parse_json_config(config_path=json_config_path)
        logger.debug(f"Parsed JSON config: {json_config_obj}")

        task_config = config_def.build_task_config(
            json_config_obj=json_config_obj,
            player_number=player_number,
            result_dir=result_dir
        )
        logger.debug(f"Task config: {vars(task_config)}")

        return task_config
    except Exception as e:
        logger.error(f"Error preparing config: {str(e)}")
        logger.debug(f"Stack trace: {traceback.format_exc()}")
        raise

def move_to_experiment_dir(task_config: config_def.TaskConfig):
    """Change the current working directory to the directory containing the code of the evaluation framework."""
    logger = logging.getLogger('ExperimentRunner')
    logger.info(f"Changing to experiment directory: {task_config.abs_path_to_code_dir}")

    try:
        os.chdir(task_config.abs_path_to_code_dir)
        logger.debug(f"Current working directory: {os.getcwd()}")
    except Exception as e:
        logger.error(f"Error changing directory: {str(e)}")
        logger.debug(f"Stack trace: {traceback.format_exc()}")
        raise

def compile_script_with_args(task_config: config_def.TaskConfig):
    """Executes the Script compilation phase of the experiment."""
    logger = logging.getLogger('ExperimentRunner')
    logger.info("Compiling script")

    try:
        compiler_args = task_config.compiler_args if task_config.compiler_args is not None else \
            runner_defs.CompilerArguments[task_config.protocol_setup.name].value
        logger.debug(f"Compiler arguments: {compiler_args}")

        comp_runner = runner_defs.CompilerRunner(
            script_name=task_config.script_name,
            script_args=task_config.script_args,
            compiler_args=compiler_args,
            code_dir=task_config.abs_path_to_code_dir
        )
        logger.debug(f"Compiler runner: {vars(comp_runner)}")

        comp_runner.run()
        logger.info("Script compilation completed")
    except Exception as e:
        logger.error(f"Error compiling script: {str(e)}")
        logger.debug(f"Stack trace: {traceback.format_exc()}")
        raise

def run_script_with_args(task_config: config_def.TaskConfig, output_prefix: str):
    """Executes the compiled script and configures the chosen MPC protocol VM."""
    logger = logging.getLogger('ExperimentRunner')
    logger.info(f"Running script with output prefix: {output_prefix}")

    try:
        script_runner_constr: runner_defs.ScriptBaseRunner = runner_defs.ProtocolRunners[task_config.protocol_setup.name].value
        script_runner_obj = script_runner_constr(
            output_prefix=output_prefix,
            script_name=task_config.script_name,
            args=task_config.script_args,
            player_0_host=task_config.player_0_hostname,
            player_id=task_config.player_id,
            custom_prime=task_config.custom_prime,
            custom_prime_length=task_config.custom_prime_length,
            player_count=task_config.player_count,
            program_args=task_config.program_args,
        )
        logger.debug(f"Script runner: {vars(script_runner_obj)}")

        script_runner_obj.run()
        logger.info("Script execution completed")
    except Exception as e:
        logger.error(f"Error running script: {str(e)}")
        logger.debug(f"Stack trace: {traceback.format_exc()}")
        raise

def capture_output(task_config: config_def.TaskConfig, output_prefix: str):
    """Captures the raw textual output using the OutputCapture class."""
    logger = logging.getLogger('ExperimentRunner')
    logger.info(f"Capturing output with prefix: {output_prefix}")

    try:
        result_dir_path = os.path.join(task_config.result_dir, DEFAULT_RESULT_FOLDER)
        logger.debug(f"Result directory: {result_dir_path}")

        out_cap_obj = out_cap.OutputCapture(
            output_prefix=output_prefix,
            result_dir=result_dir_path,
            player_id=task_config.player_id
        )
        logger.debug(f"Output capture object: {vars(out_cap_obj)}")

        out_cap_obj.capture_output()
        logger.info("Output capture completed")
    except Exception as e:
        logger.error(f"Error capturing output: {str(e)}")
        logger.debug(f"Stack trace: {traceback.format_exc()}")
        raise

def clean_workspace(task_config: config_def.TaskConfig, output_prefix: str):
    """Cleans the workspace from the output files and Player-Data folders."""
    logger = logging.getLogger('ExperimentRunner')
    logger.info(f"Cleaning workspace with output prefix: {output_prefix}")

    try:
        cleaner_obj = clr.Cleaner(
            code_dir=task_config.abs_path_to_code_dir,
            output_prefix=output_prefix,
            remove_input_files=task_config.remove_input_files
        )
        logger.debug(f"Cleaner object: {vars(cleaner_obj)}")

        cleaner_obj.clean()
        logger.info("Workspace cleaning completed")
    except Exception as e:
        logger.error(f"Error cleaning workspace: {str(e)}")
        logger.debug(f"Stack trace: {traceback.format_exc()}")
        raise

def sync_servers(task_config: config_def.TaskConfig):
    """Synchronize servers by pinging the host server."""
    logger = logging.getLogger('ExperimentRunner')
    logger.info("Synchronizing servers")

    try:
        rendezvouz.sync(task_config.player_0_hostname, task_config.player_count, task_config.player_id)
        logger.info("Server synchronization completed")
    except Exception as e:
        logger.error(f"Error synchronizing servers: {str(e)}")
        logger.debug(f"Stack trace: {traceback.format_exc()}")
        raise

def prove_commitment_opening(task_config, output_prefix):
    """Prove commitment opening for consistency checks."""
    logger = logging.getLogger('ExperimentRunner')
    logger.info("Starting prove commitment opening")

    if task_config.consistency_args is None:
        logger.info("No consistency check arguments specified. Skipping consistency check.")
        return

    if task_config.consistency_args.type != "pc":
        logger.info(f"Consistency check type is {task_config.consistency_args.type}. Skipping PC phase.")
        return

    result_dir_path = os.path.join(task_config.result_dir, DEFAULT_RESULT_FOLDER)
    logger.debug(f"Result directory for consistency check: {result_dir_path}")

    try:
        # GEN PP
        if task_config.consistency_args.gen_pp:
            executable = f"target/release/gen_pp_{task_config.consistency_args.pc}"
            args = {"num-args": task_config.consistency_args.pp_args}
            args_str = " ".join([f"--{k} {v}" for k, v in args.items()])
            executable_str = f"{executable} {args_str}"
            logger.info(f"Generating public parameters with command: {executable_str}")

            consistency_gen_pp_output_file = open(os.path.join(result_dir_path, "consistency_gen_pp.log"), "w+")
            result = subprocess.run(
                executable_str,
                shell=True,
                cwd=task_config.consistency_args.abs_path_to_code_dir,
                check=True,
                stdout=consistency_gen_pp_output_file,
                stderr=consistency_gen_pp_output_file,
            )
            logger.debug(f"Gen PP command output: {result}")
            consistency_gen_pp_output_file.close()

        if task_config.sleep_time > 0:
            logger.info(f"Sleeping for {task_config.sleep_time} seconds to allow gen commitment process.")
            time.sleep(task_config.sleep_time)

        # GEN COMMITMENTS
        executable = f"target/release/gen_commitments_{task_config.consistency_args.pc}"
        args = {
            "hosts": task_config.consistency_args.hosts_file,
            "party": task_config.player_id,
            "player-input-binary-path": f"{task_config.abs_path_to_code_dir}/MP-SPDZ/Player-Data/Input-Binary-P{task_config.player_id}-0",
            "save": "",
            "debug": "",
        }
        args_str = " ".join([f"--{k} {v}" for k, v in args.items()])
        executable_str = f"{executable} {args_str}"
        logger.info(f"Running consistency check with command: {executable_str}")

        consistency_gen_commitments_output_file = open(os.path.join(result_dir_path, "consistency_gen_commitments.log"), "w+")
        result = subprocess.run(
            executable_str,
            shell=True,
            cwd=task_config.consistency_args.abs_path_to_code_dir,
            check=True,
            stdout=consistency_gen_commitments_output_file,
            stderr=consistency_gen_commitments_output_file,
        )
        logger.debug(f"Gen commitments command output: {result}")
        consistency_gen_commitments_output_file.close()

        if task_config.sleep_time > 0:
            logger.info(f"Sleeping for {task_config.sleep_time} seconds to allow commitment generation.")
            time.sleep(task_config.sleep_time)

        result_file_name = f"consistency_poly_eval.log"
        result_file_path = os.path.join(result_dir_path, result_file_name)
        if not os.path.exists(result_file_path):
            logger.error(f"Could not find mpspdz output file: {result_file_path}")
            raise FileNotFoundError(f"Expected to find {result_file_path}")

        if task_config.sleep_time > 0:
            logger.info(f"Sleeping for {task_config.sleep_time} seconds to allow gen commitment process.")
            time.sleep(task_config.sleep_time)

        # PROVE_VERIFY
        executable = f"target/release/prove_verify_{task_config.consistency_args.pc}"
        args = {
            "hosts": task_config.consistency_args.hosts_file,
            "party": task_config.player_id,
            "mpspdz-output-file": result_file_path,
            "debug": "",
        }
        if task_config.consistency_args.prover_party is not None:
            args['prover-party'] = task_config.consistency_args.prover_party
        args_str = " ".join([f"--{k} {v}" for k, v in args.items()])
        executable_str = f"{executable} {args_str}"
        logger.info(f"Running consistency check with command: {executable_str}")

        consistency_prove_verify_output_file = open(os.path.join(result_dir_path, "consistency_prove_verify.log"), "w+")
        result = subprocess.run(
            executable_str,
            shell=True,
            cwd=task_config.consistency_args.abs_path_to_code_dir,
            check=True,
            stdout=consistency_prove_verify_output_file,
            stderr=consistency_prove_verify_output_file,
        )
        logger.debug(f"Prove verify command output: {result}")
        consistency_prove_verify_output_file.close()

        logger.info("Prove commitment opening completed")
    except Exception as e:
        logger.error(f"Error in prove commitment opening: {str(e)}")
        logger.debug(f"Stack trace: {traceback.format_exc()}")
        raise

def cerebro_verify(task_config, input_size):
    """Verify cerebro exponentiation."""
    logger = logging.getLogger('ExperimentRunner')
    logger.info(f"Running cerebro verification with input size: {input_size}")

    try:
        executable = f"target/release/exponentiate_cerebro"
        args = {
            "n-parameters": input_size,
            "debug": "",
        }
        args_str = " ".join([f"--{k} {v}" for k, v in args.items()])
        executable_str = f"{executable} {args_str}"
        logger.info(f"Running cerebro exponentiate with command: {executable_str}")

        result_dir_path = os.path.join(task_config.result_dir, DEFAULT_RESULT_FOLDER)
        consistency_cerebro_verify_output_file = open(os.path.join(result_dir_path, "consistency_cerebro_verify.log"), "a+")
        result = subprocess.run(
            executable_str,
            shell=True,
            cwd=task_config.consistency_args.abs_path_to_code_dir,
            check=True,
            stdout=consistency_cerebro_verify_output_file,
            stderr=consistency_cerebro_verify_output_file,
        )
        logger.debug(f"Cerebro verify command output: {result}")
        consistency_cerebro_verify_output_file.close()

        logger.info("Cerebro verification completed")
    except Exception as e:
        logger.error(f"Error in cerebro verification: {str(e)}")
        logger.debug(f"Stack trace: {traceback.format_exc()}")
        raise

def convert_shares(task_config, output_prefix):
    """Convert shares for consistency checks or commitment."""
    logger = logging.getLogger('ExperimentRunner')
    logger.info(f"Converting shares with output prefix: {output_prefix}")

    if task_config.commit_output != True and task_config.consistency_args is None:
        logger.info("No commit or consistency check specified. No need to convert shares.")
        return

    protocol = task_config.protocol_setup
    logger.debug(f"Protocol: {protocol}")

    try:
        # Determine executable and conversion prefix
        conversion_not_needed = protocol in [
            config_def.ProtocolChoices.REP_FIELD_PARTY,
            config_def.ProtocolChoices.SY_REP_FIELD_PARTY,
            config_def.ProtocolChoices.LOWGEAR_PARTY,
            config_def.ProtocolChoices.HIGHGEAR_PARTY,
            config_def.ProtocolChoices.MASCOT_PARTY,
            config_def.ProtocolChoices.SEMI_PARTY
        ]
        logger.debug(f"Conversion not needed: {conversion_not_needed}")

        executable_prefix = None
        conversion_prefix = None
        need_input_sharing = False
        if protocol == config_def.ProtocolChoices.REPLICATED_RING_PARTY_X:
            executable_prefix = "rep"
            conversion_prefix = "rep-ring"
        elif protocol == config_def.ProtocolChoices.REP_FIELD_PARTY:
            executable_prefix = "rep"
            conversion_prefix = "rep-field"
        elif protocol == config_def.ProtocolChoices.SY_REP_RING_PARTY:
            executable_prefix = "sy-rep"
            conversion_prefix = "sy-rep-ring"
        elif protocol == config_def.ProtocolChoices.SY_REP_FIELD_PARTY:
            executable_prefix = "sy-rep"
            conversion_prefix = "sy-rep-field"
        elif protocol == config_def.ProtocolChoices.SEMI_PARTY:
            executable_prefix = "semi"
            conversion_prefix = "semi"
            need_input_sharing = task_config.custom_prime is not None
        elif protocol in [config_def.ProtocolChoices.LOWGEAR_PARTY, config_def.ProtocolChoices.HIGHGEAR_PARTY, config_def.ProtocolChoices.MASCOT_PARTY]:
            executable_prefix = "mascot"
            conversion_prefix = "mascot"
            need_input_sharing = task_config.custom_prime is not None
        else:
            logger.error(f"Cannot convert from protocol {protocol}")
            raise ValueError(f"Cannot convert from protocol {protocol}")

        logger.debug(f"Executable prefix: {executable_prefix}, Conversion prefix: {conversion_prefix}, Need input sharing: {need_input_sharing}")

        player_data_dir = os.path.join(task_config.abs_path_to_code_dir, "MP-SPDZ", "Player-Data")
        player_input_list, output_data, total_output_length = format_config.get_total_share_length(player_data_dir, task_config.player_count)
        logger.debug(f"Player input list: {player_input_list}, Output data: {output_data}, Total output length: {total_output_length}")

        debug_flag = "-d" if task_config.convert_debug else ""
        split_flag = "-sp" if task_config.consistency_args.use_split else ""
        spdz_args_str = f"-p {task_config.player_id} -N {task_config.player_count} -h {task_config.player_0_hostname}"
        logger.debug(f"SPDZ args: {spdz_args_str}, Debug flag: {debug_flag}, Split flag: {split_flag}")

        total_input_length = 0
        player_input_counter = {i: [] for i in range(len(player_input_list))}

        if task_config.consistency_args is not None:
            if (task_config.convert_ring_if_needed and
                    task_config.consistency_args.type not in ["sha3", "sha3s"]):
                executable = f"./{conversion_prefix}-switch-party.x"
                if need_input_sharing:
                    logger.info("Performing input sharing instead of conversion.")
                    executable = f"./{executable_prefix}-share-party.x"

                input_parts = []
                for player_id, p_inputs_objs in enumerate(player_input_list):
                    if p_inputs_objs is None:
                        input_parts.append("-i 0")
                        player_input_counter[player_id].append(0)
                    else:
                        types = []
                        for p_input_obj in p_inputs_objs:
                            p_inputs = p_input_obj["items"]
                            player_input_cnt = 0
                            for p_input in p_inputs:
                                if p_input["type"] == "sfix":
                                    types.append(f"f{p_input['length']}")
                                elif p_input["type"] == "sint":
                                    types.append(f"i{p_input['length']}")
                                else:
                                    logger.error(f"Unknown type {p_input['type']}")
                                    raise ValueError(f"Unknown type {p_input['type']}")
                                total_input_length += p_input['length']
                                player_input_cnt += p_input['length']
                            player_input_counter[player_id].append(player_input_cnt)
                        input_parts.append(f"-i {','.join(types)}")
                input_str = " ".join(input_parts)
                logger.debug(f"Input string: {input_str}")

                executable_str = f"{executable} {spdz_args_str} --n_bits {task_config.convert_ring_bits} --n_threads {task_config.convert_n_threads} --chunk_size {task_config.convert_chunk_size} {split_flag} {debug_flag} {input_str}"
                logger.info(f"Converting input shares with command: {executable_str}")

                result_dir_path = os.path.join(task_config.result_dir, DEFAULT_RESULT_FOLDER)
                convert_shares_phase = open(os.path.join(result_dir_path, "consistency_convert_shares.log"), "a+")
                try:
                    result = subprocess.run(
                        executable_str,
                        shell=True,
                        cwd=os.path.join(task_config.abs_path_to_code_dir, "MP-SPDZ"),
                        check=True,
                        stdout=convert_shares_phase,
                        stderr=convert_shares_phase,
                    )
                    logger.debug(f"Convert shares command output: {result}")
                except subprocess.CalledProcessError as e:
                    logger.error(f"Error converting shares: {str(e)}")
                    logger.info(f"Continuing without converting shares. Conversion not needed: {conversion_not_needed}")
                    copy_transaction_files(conversion_not_needed, task_config)
                finally:
                    convert_shares_phase.close()

                if task_config.sleep_time > 0:
                    logger.info(f"Sleeping for {task_config.sleep_time} seconds to allow process completion.")
                    time.sleep(task_config.sleep_time)

            if task_config.consistency_args.type == "cerebro":
                logger.info("Invoking cerebro to compute commitments.")
                compile_cerebro_with_args(task_config, "single_cerebro")
                run_cerebro_with_args(task_config, "single_cerebro", output_prefix, DEFAULT_RESULT_FOLDER, "input")

                for player_id, inputs in player_input_counter.items():
                    for input_size in inputs:
                        if input_size > 0:
                            logger.info(f"CEREBRO_INPUT_SIZE=({player_id},{input_size})")
                            cerebro_verify(task_config, input_size)
            elif task_config.consistency_args.type == "cerebro_ec":
                for player_id, inputs in player_input_counter.items():
                    for input_size in inputs:
                        if input_size > 0:
                            logger.info(f"CEREBRO_INPUT_SIZE=({player_id},{input_size})")
                            cerebro_verify(task_config, input_size)
            elif task_config.consistency_args.type == "sha3":
                logger.info("Computing sha3-based commitments, nothing else needed.")
            elif task_config.consistency_args.type == "sha3s":
                logger.info("Computing sha3-based commitments in separate script.")
                compile_sha3_with_args(task_config, "standalone_sha3", True)
                run_sha3_with_args(task_config, "standalone_sha3", output_prefix, DEFAULT_RESULT_FOLDER, "input")
            else:
                logger.info(f"Computing polynomials for inputs: {player_input_counter}")

                eval_point = task_config.consistency_args.eval_point
                if eval_point is None and task_config.consistency_args.single_random_eval_point:
                    logger.info("Using single random eval point across poly eval runs.")

                input_counter = 0
                for party_id, player_input_counts in player_input_counter.items():
                    if not player_input_counts:
                        logger.info(f"Skipping party {party_id} (no input).")
                        continue
                    for player_input_count in player_input_counts:
                        if player_input_count == 0:
                            logger.info(f"Skipping party {party_id} (player_input_count=0).")
                            continue
                        executable = f"./{executable_prefix}-pe-party.x"
                        args = {
                            "n_shares": player_input_count,
                            "start": input_counter,
                            "input_party_i": party_id,
                        }
                        if eval_point is not None:
                            args['eval_point'] = eval_point

                        args_str = " ".join([f"--{k} {v}" for k, v in args.items()])
                        executable_str = f"{executable} {spdz_args_str} {args_str}"
                        logger.info(f"Computing polynomial for player {party_id} with command: {executable_str}")

                        result_dir_path = os.path.join(task_config.result_dir, DEFAULT_RESULT_FOLDER)
                        poly_eval_phase = open(os.path.join(result_dir_path, "consistency_poly_eval.log"), "a+")
                        result = subprocess.run(
                            executable_str,
                            shell=True,
                            cwd=os.path.join(task_config.abs_path_to_code_dir, "MP-SPDZ"),
                            check=True,
                            stdout=poly_eval_phase,
                            stderr=poly_eval_phase,
                        )
                        logger.debug(f"Poly eval command output: {result}")
                        poly_eval_phase.close()

                        if task_config.consistency_args.single_random_eval_point and eval_point is None:
                            logger.info("Parsing eval point")
                            eval_point = find_eval_point(os.path.join(result_dir_path, "consistency_poly_eval.log"))
                            logger.debug(f"Parsed eval point: {eval_point}")

                        input_counter += player_input_count

                        if task_config.sleep_time > 0:
                            logger.info(f"Sleeping for {task_config.sleep_time} seconds to allow process completion.")
                            time.sleep(task_config.sleep_time)

                assert input_counter == total_input_length, f"Expected to process {total_input_length} shares, but processed {input_counter}"

        if task_config.commit_output:
            if task_config.consistency_args.type == "sha3":
                logger.info("Computing sha3 hash in script, nothing else needed.")
            elif task_config.consistency_args.type == "sha3s":
                logger.info("Computing sha3 hash in separate script.")
                compile_sha3_with_args(task_config, "standalone_sha3", False)
                run_sha3_with_args(task_config, "standalone_sha3", output_prefix, DEFAULT_RESULT_FOLDER, "output")
            else:
                if total_output_length == 0:
                    logger.warning("No output to convert. Is this a mistake?")

                if task_config.convert_ring_if_needed:
                    executable = f"./{conversion_prefix}-switch-party.x"
                    args = {
                        "n_shares": total_output_length,
                        "n_bits": task_config.convert_ring_bits,
                        "n_threads": task_config.convert_n_threads,
                        "chunk_size": task_config.convert_chunk_size,
                        "out_start": total_input_length,
                    }
                    args_str = " ".join([f"--{k} {v}" for k, v in args.items()])
                    executable_str = f"{executable} {spdz_args_str} {split_flag} {debug_flag} {args_str}"
                    logger.info(f"Converting shares with command: {executable_str}")

                    result_dir_path = os.path.join(task_config.result_dir, DEFAULT_RESULT_FOLDER)
                    convert_shares_phase = open(os.path.join(result_dir_path, "consistency_convert_shares.log"), "a+")
                    try:
                        result = subprocess.run(
                            executable_str,
                            shell=True,
                            cwd=os.path.join(task_config.abs_path_to_code_dir, "MP-SPDZ"),
                            check=True,
                            stdout=convert_shares_phase,
                            stderr=convert_shares_phase,
                        )
                        logger.debug(f"Convert shares command output: {result}")
                    except subprocess.CalledProcessError as e:
                        logger.error(f"Error converting shares: {str(e)}")
                        logger.info(f"Continuing without converting shares. Conversion not needed: {conversion_not_needed}")
                        copy_transaction_files(conversion_not_needed, task_config)
                    finally:
                        convert_shares_phase.close()

                    if task_config.sleep_time > 0:
                        logger.info(f"Sleeping for {task_config.sleep_time} seconds to allow process completion.")
                        time.sleep(task_config.sleep_time)

                if task_config.consistency_args.type == "cerebro":
                    logger.info("Invoking cerebro to compute commitments (output).")
                    compile_cerebro_with_args(task_config, "single_cerebro")
                    run_cerebro_with_args(task_config, "single_cerebro", output_prefix, DEFAULT_RESULT_FOLDER, "output")

                    for c in output_data:
                        logger.info(f"CEREBRO_OUTPUT_SIZE=({c['object_type']},{c['length']})")
                else:
                    pass

                args = {c['object_type']: c['length'] for c in output_data} if task_config.consistency_args.type in ["pc", "cerebro_ec"] else {}
                args['s'] = total_input_length
                args_str = " ".join([f"-{k} {v}" for k, v in args.items()]) if args else ""

                executable = f"./{executable_prefix}-pc-party.x"
                executable_str = f"{executable} {spdz_args_str} {args_str}"
                logger.info(f"Computing and signing commitments with command: {executable_str}")

                result_dir_path = os.path.join(task_config.result_dir, DEFAULT_RESULT_FOLDER)
                poly_commit_phase = open(os.path.join(result_dir_path, "consistency_poly_commit.log"), "w+")
                result = subprocess.run(
                    executable_str,
                    shell=True,
                    cwd=os.path.join(task_config.abs_path_to_code_dir, "MP-SPDZ"),
                    check=True,
                    stdout=poly_commit_phase,
                    stderr=poly_commit_phase,
                )
                logger.debug(f"Poly commit command output: {result}")
                poly_commit_phase.close()

        logger.info("Share conversion completed")
    except Exception as e:
        logger.error(f"Error in share conversion: {str(e)}")
        logger.debug(f"Stack trace: {traceback.format_exc()}")
        raise

def copy_transaction_files(conversion_not_needed, task_config):
    """Copy transaction files if conversion is not needed."""
    logger = logging.getLogger('ExperimentRunner')
    if not conversion_not_needed:
        logger.info("Conversion needed, skipping transaction file copy.")
        return

    logger.info("Copying persistence files")
    persistence_data_path = os.path.join(task_config.abs_path_to_code_dir, "MP-SPDZ", "Persistence")
    logger.debug(f"Persistence data path: {persistence_data_path}")

    try:
        for file_name in os.listdir(persistence_data_path):
            file_path = os.path.join(persistence_data_path, file_name)
            if os.path.isfile(file_path) and re.match(r'Transactions-P(\d*)\.data', file_name):
                filename_suffix = file_name.split(".")[0] + "-P251" + "." + file_name.split(".")[1]
                logger.info(f"Copying {file_name} to {filename_suffix}")
                shutil.copyfile(file_path, os.path.join(persistence_data_path, filename_suffix))
        logger.info("Transaction file copy completed")
    except Exception as e:
        logger.error(f"Error copying transaction files: {str(e)}")
        logger.debug(f"Stack trace: {traceback.format_exc()}")
        raise

def find_eval_point(filename):
    """Find evaluation point in log file."""
    logger = logging.getLogger('ExperimentRunner')
    logger.info(f"Parsing eval point from file: {filename}")

    eval_point_regex = r"input_consistency_player_(\d*)_eval=\((.*),(.*)\)"
    try:
        with open(filename, "r") as f:
            for line in f.readlines():
                match = re.match(eval_point_regex, line)
                if match:
                    eval_point = match.group(2)
                    logger.info(f"Found eval point: {eval_point}")
                    return eval_point
        logger.error("Could not find eval point in log file.")
        raise ValueError("Could not find eval point in log file.")
    except Exception as e:
        logger.error(f"Error parsing eval point: {str(e)}")
        logger.debug(f"Stack trace: {traceback.format_exc()}")
        raise

def clean_persistence_data(code_dir):
    """Clean the persistence data folder in the MP-SPDZ folder."""
    logger = logging.getLogger('ExperimentRunner')
    logger.info("Cleaning persistence data")

    persistence_data_path = os.path.join(code_dir, "MP-SPDZ/Persistence")
    logger.debug(f"Persistence data path: {persistence_data_path}")

    try:
        shutil.rmtree(persistence_data_path, ignore_errors=True)
        os.mkdir(persistence_data_path)
        logger.info("Persistence data cleaning completed")
    except Exception as e:
        logger.error(f"Error cleaning persistence data: {str(e)}")
        logger.debug(f"Stack trace: {traceback.format_exc()}")
        raise

@click.command()
@click.option("--player-number", "player_number", required=True, type=float, help="The player number of the machine executing the experiment")
def cli(player_number):
    """This is the experiment runner script that will run the MP-SPDZ experiments for this framework."""
    logger = setup_logging(player_number, os.getcwd())
    logger.info(f"Starting experiment runner for player {player_number}")
    logger.debug(f"Command line arguments: {sys.argv}")

    result_dir = os.getcwd()
    try:
        task_config = prepare_config(player_number=player_number, result_dir=result_dir)
        move_to_experiment_dir(task_config=task_config)

        if "compile" in task_config.stage:
            logger.info("Executing compile stage")
            clean_workspace(task_config=task_config, output_prefix=None)
            compile_script_with_args(task_config=task_config)

        if "run" in task_config.stage:
            logger.info("Executing run stage")
            clean_persistence_data(task_config.abs_path_to_code_dir)
            output_prefix = generate_random_prefix()
            run_script_with_args(task_config=task_config, output_prefix=output_prefix)
            sync_servers(task_config=task_config)
            convert_shares(task_config=task_config, output_prefix=output_prefix)
            prove_commitment_opening(task_config=task_config, output_prefix=output_prefix)
            capture_output(task_config=task_config, output_prefix=output_prefix)

        logger.info("Experiment runner completed successfully")
    except Exception as e:
        logger.error(f"Experiment runner failed: {str(e)}")
        logger.debug(f"Stack trace: {traceback.format_exc()}")
        raise

if __name__ == "__main__":
    cli()