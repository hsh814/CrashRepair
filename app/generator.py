from pathlib import Path
from typing import List, Dict
from six.moves import cStringIO
from pysmt.shortcuts import Not, And, Or
import os
from pysmt.smtlib.parser import SmtLibParser
from pysmt.typing import BV32, BV8, ArrayType
from pysmt.shortcuts import write_smtlib, get_model, Symbol, is_unsat, is_sat
from app import emitter, values, reader, definitions, extractor, oracle, utilities
import re
import struct
import random
import copy

File_Log_Path = "/tmp/log_sym_path"
File_Ktest_Path = "/tmp/concolic.ktest"


def generate_flipped_path(ppc):
    """
    This function will check if a selected path is feasible
           ppc : partial path conditoin at chosen control loc
           chosen_control_loc: branch location selected for flip
           returns satisfiability of the negated path
    """
    parser = SmtLibParser()
    new_path = None
    try:
        script = parser.get_script(cStringIO(ppc))
        formula = script.get_last_formula()
        prefix = formula.arg(0)
        constraint = formula.arg(1)
        new_path = And(prefix, Not(constraint))
        assert str(new_path.serialize()) != str(formula.serialize())
    except Exception as ex:
        emitter.debug("Pysmt parser error, skipping path flip")
    finally:
        return new_path


def generate_true_constraint(path_constraint):
    true_constraint = None
    if path_constraint.is_and() or path_constraint.is_or():
        prefix = None
        while path_constraint.is_and() or path_constraint.is_or():
            constraint = path_constraint.arg(1)
            constraint_str = str(constraint.serialize())
            if "angelic!bool" in constraint_str:
                model = generate_model(constraint)
                for var_name, byte_list in model:
                    if "angelic!bool" in var_name:
                        value = utilities.get_signed_value(byte_list)
                        if value == 0:
                            constraint = Not(constraint)

            if true_constraint:
                if path_constraint.is_and():
                    true_constraint = And(true_constraint, constraint)
                elif  path_constraint.is_or():
                    true_constraint = Or(true_constraint, constraint)
            else:
                true_constraint = constraint
            prefix= path_constraint.arg(0)
            if prefix.is_and() or prefix.is_or():
                path_constraint = prefix
            else:
                prefix_str = str(prefix.serialize())
                if "angelic!bool" in prefix_str:
                    model = generate_model(prefix)
                    for var_name, byte_list in model:
                        if "angelic!bool" in var_name:
                            value = utilities.get_signed_value(byte_list)
                            if value == 0:
                                prefix = Not(prefix)
        if path_constraint.is_and():
            true_constraint = And(true_constraint, prefix)
        elif path_constraint.is_or():
            true_constraint = Or(true_constraint, prefix)

    else:
        model = generate_model(path_constraint)
        for var_name in model:
            byte_list = model[var_name]
            if "angelic!bool" in var_name:
                value = utilities.get_signed_value(byte_list)
                if value == 0:
                    true_constraint = Not(path_constraint)
                else:
                    true_constraint = path_constraint
    return true_constraint


def generate_false_constraint(path_constraint):
    false_constraint = None
    if path_constraint.is_and() or path_constraint.is_or():
        prefix = None
        while path_constraint.is_and() or path_constraint.is_or():
            constraint = path_constraint.arg(1)
            constraint_str = str(constraint.serialize())
            if "angelic!bool" in constraint_str:
                model = generate_model(constraint)
                for var_name, byte_list in model:
                    if "angelic!bool" in var_name:
                        value = utilities.get_signed_value(byte_list)
                        if value != 0:
                            constraint = Not(constraint)

            if false_constraint:
                if path_constraint.is_and():
                    false_constraint = And(false_constraint, constraint)
                elif path_constraint.is_or():
                    false_constraint = Or(false_constraint, constraint)
            else:
                false_constraint = constraint
            prefix = path_constraint.arg(0)
            if prefix.is_and() or prefix.is_or():
                path_constraint = prefix
            else:
                prefix_str = str(prefix.serialize())
                if "angelic!bool" in prefix_str:
                    model = generate_model(prefix)
                    for var_name, byte_list in model:
                        if "angelic!bool" in var_name:
                            value = utilities.get_signed_value(byte_list)
                            if value != 0:
                                prefix = Not(prefix)
        if path_constraint.is_and():
            false_constraint = And(false_constraint, prefix)
        elif path_constraint.is_or():
            false_constraint = Or(false_constraint, prefix)

    else:
        model = generate_model(path_constraint)
        for var_name in model:
            byte_list = model[var_name]
            if "angelic!bool" in var_name:
                value = utilities.get_signed_value(byte_list)
                if value != 0:
                    false_constraint = Not(path_constraint)
                else:
                    false_constraint = path_constraint
    return false_constraint


def generate_special_paths(con_loc, ppc_str):
    parser = SmtLibParser()
    special_list = []
    script = parser.get_script(cStringIO(ppc_str))
    path_condition = script.get_last_formula()
    angelic_count = int(len(re.findall("angelic!(.+?)!0", str(path_condition.serialize()))) / 4)
    if angelic_count > 1:
        false_path = generate_false_path(path_condition)
        true_path = generate_true_path(path_condition)
        if true_path:
            special_list.append((con_loc, true_path, len(str(true_path.serialize()))))
        if false_path:
            special_list.append((con_loc, false_path, len(str(false_path.serialize()))))
    return special_list


def generate_angelic_val(klee_out_dir, arg_list, poc_path):
    file_list = [os.path.join(klee_out_dir, f) for f in os.listdir(klee_out_dir) if os.path.isfile(os.path.join(klee_out_dir,f))]
    ref_file_path = None
    largest_file_path = None
    largest_file_size = 0
    for file_path in file_list:
        file_size = os.path.getsize(file_path)
        if ".smt2" in file_path:
            if file_size > largest_file_size:
                largest_file_size = file_size
                largest_file_path = file_path
        if ".err" in file_path:
            ref_file_path = file_path.split(".")[0] + ".smt2"
            break
    if ref_file_path is None:
        if largest_file_path:
            ref_file_path = largest_file_path
        else:
            ref_file_path = klee_out_dir + "/test000001.smt2"
    sym_path = extractor.extract_formula_from_file(ref_file_path)
    input_arg_list, input_var_list = generate_new_input(sym_path, arg_list, poc_path)
    return input_arg_list, input_var_list


def generate_mask_bytes(klee_out_dir, poc_path):
    mask_byte_list = list()
    log_path = klee_out_dir + "/concrete.log"
    concretized_byte_list = reader.collect_concretized_bytes(log_path)
    smt2_file_path = klee_out_dir + "/test000001.smt2"
    control_byte_list = reader.collect_bytes_from_smt2(smt2_file_path)
    emitter.data("Control Byte List", control_byte_list)
    fixed_byte_list = list()
    if "A-data" in concretized_byte_list:
        influence_byte_list = sorted(list(concretized_byte_list["A-data"]))
        emitter.data("Influencing Byte List", influence_byte_list)
    fixed_byte_list = control_byte_list
    if poc_path:
        byte_length = os.path.getsize(poc_path)
        for i in range(0, byte_length):
            if i not in fixed_byte_list:
                mask_byte_list.append(i)
    return sorted(mask_byte_list)


def generate_model(formula):
    """
           This function will invoke PySMT APIs to solve the provided formula and return the byte list of the model
           Arguments:
               formula: smtlib formatted formula
    """
    # emitter.normal("\textracting z3 model")
    model = get_model(formula)
    if model is None:
        return None
    path_script = "/tmp/z3_script_model"
    write_smtlib(formula, path_script)
    with open(path_script, "r") as script_file:
        script_lines = script_file.readlines()
    script = "".join(script_lines)
    var_list = set(re.findall("\(declare-fun (.+?) \(\)", script))
    sym_var_list = dict()
    for var_name in var_list:
        # sym_var_list[var_name] = dict()
        if "const_" in var_name and not "const_arr" in var_name:
            sym_def = Symbol(var_name, BV32)
            if sym_def not in model:
                continue
            x = model[sym_def]
            byte_list = dict()
            default_value = x.bv_signed_value()
            byte_list[0] = default_value
        else:
            sym_def = Symbol(var_name, ArrayType(BV32, BV8))
            if sym_def not in model:
                continue
            x = model[sym_def].simplify()
            byte_list = dict()
            value_array_map = x.array_value_assigned_values_map()
            default_value = int(str(x.array_value_default()).split("_")[0])
            if not value_array_map:
                byte_list[0] = default_value
            else:
                for idx, val in value_array_map.items():
                    index = int(str(idx).split("_")[0])
                    value = int(str(val).split("_")[0])
                    byte_list[index] = value

                max_index = max(list(byte_list.keys()))
                if var_name in values.LIST_BIT_LENGTH:
                    array_size = values.LIST_BIT_LENGTH[var_name] - 1
                    if var_name in ["A-data"]:
                        array_size = max_index

                else:
                    array_size = max_index + 1  # TODO: this could be wrong calculation

                if max_index == 0:
                    array_size = 2

                if var_name not in ["A-data"]:
                    for i in range(0, array_size):
                        if i not in byte_list:
                            byte_list[i] = default_value

                if var_name not in ["A-data", "A-data-stat"]:
                    for i in range(array_size - 1, -1, -1):
                        if byte_list[i] == 0:
                            byte_list.pop(i)
                        else:
                            break
        sym_var_list[var_name] = byte_list
    emitter.data("model var list", sym_var_list)
    return sym_var_list


def generate_constant_value_list(sym_path):
    gen_const_list = dict()
    gen_var_list = dict()
    const_val_list = dict()
    model = generate_model(sym_path)
    if model is None:
        return None
    for var_name in model:
        var_byte_list = model[var_name]
        if "const" in var_name:
            gen_const_list[var_name] = var_byte_list
        else:
            gen_var_list[var_name] = var_byte_list

    for const_name in gen_const_list:
        bit_vector = gen_const_list[const_name]
        const_value = utilities.get_signed_value(bit_vector)
        const_val_list[const_name] = const_value

    emitter.data("Generated Constant List", const_val_list)
    return const_val_list


def generate_new_input(sym_path, argument_list=None, poc_path=None, gen_path=None):
    gen_arg_list = dict()
    gen_var_list = dict()
    input_var_list = list()
    input_arg_dict = dict()
    input_arg_list = list()
    model = generate_model(sym_path)
    if model is None:
        return None, None
    for var_name in model:
        var_byte_list = model[var_name]
        if "arg" in var_name:
            gen_arg_list[var_name] = var_byte_list
        else:
            gen_var_list[var_name] = var_byte_list
    mask_list = values.CONF_MASK_ARG
    mask_map = dict()
    if values.CONF_MASK_ARG:
        min_val = 0
        new_idx = 0
        max_val = len(argument_list)
        for idx in range(min_val, max_val):
            if str(idx) not in mask_list:
                mask_map[new_idx] = idx
                new_idx = new_idx + 1

    for arg_name in gen_arg_list:
        bit_vector = gen_arg_list[arg_name]
        arg_index = int(str(arg_name).replace("arg", ""))
        arg_str = utilities.get_str_value(bit_vector)
        arg_value = utilities.get_signed_value(bit_vector) - 48
        arg_index_orig = arg_index
        if values.CONF_MASK_ARG:
            arg_index_orig = mask_map[arg_index_orig]
        # print(arg_name, arg_index, arg_value)
        if str(argument_list[arg_index_orig]).isnumeric() or \
                (not str(argument_list[arg_index_orig]).isalpha() and any(op in str(argument_list[arg_index_orig]) for op in ["+", "-", "/", "*"])):
            input_arg_dict[arg_index] = str(arg_value)
            # emitter.debug(arg_name, arg_value)
        else:
            arg_str_filtered = str(arg_str).replace("<", "a").replace("&", "s").replace(">", "a").replace("'", "a")
            input_arg_dict[arg_index] = arg_str_filtered
            # emitter.debug(arg_name, arg_str)

    # fill random values if not generated
    offset = 0
    for arg in argument_list:
        index = list(argument_list).index(arg) - offset
        if "$POC" in arg:
            input_arg_list.append(str(argument_list[index]))
            offset = 1
        elif str(index) in values.CONF_MASK_ARG:
            input_arg_list.append(arg)
        elif index in input_arg_dict:
            input_arg_list.append(input_arg_dict[index])
        else:
            arg_len = len(str(argument_list[index]))
            random_value = ""
            for j in range(0, arg_len):
                random_value += chr(random.randint(32, 128))
            input_arg_list.append(random_value)

    for var_name in gen_var_list:
        bit_vector = gen_var_list[var_name]
        var_value = 0
        var_size = len(bit_vector)
        if var_name in ["A-data", "A-data-stat"]:
            if var_name == "A-data":
                generate_binary_file(bit_vector, poc_path, gen_path)
            continue
        if bit_vector:
            var_value = utilities.get_signed_value(bit_vector)
        # emitter.debug(var_name, var_value)
        if "angelic" in var_name:
            input_var_list.append({"identifier": var_name, "value": var_value, "size": 4})
        # input_var_list.append({"identifier": var_name, "value": var_value, "size": 4})

    # for var_tuple in second_var_list:
    #     var_name = var_tuple['identifier']
    #     if var_name not in gen_var_list:
    #         emitter.warning("\t[warning] variable " + var_name + " assigned random value")
    #         var_size = var_tuple['size']
    #         var_value = 0
    #         for i in range(1, var_size):
    #             var_value += ((2 << 7) << (int(i) - 1)) * random.randint(0, 255)
    #         input_var_list.append({"identifier": var_name, "value": var_value, "size": var_size})
    emitter.data("Generated Arg List", input_arg_list)
    emitter.data("Generated Var List", input_var_list)
    return input_arg_list, input_var_list


def generate_binary_file(byte_array, seed_file_path, gen_file_path=None):
    byte_list = []
    modified_index_list = []
    with open(seed_file_path, "rb") as poc_file:
        byte = poc_file.read(1)
        while byte:
            number = int(struct.unpack('>B', byte)[0])
            byte_list.append(number)
            byte = poc_file.read(1)
    mask_byte_list = values.MASK_BYTE_LIST[seed_file_path]
    emitter.data("Masked Byte List", mask_byte_list)
    for index in byte_array:
        if index not in mask_byte_list:
            byte_list[index] = byte_array[index]
            modified_index_list.append(index)
    emitter.data("Modified Byte List", modified_index_list)
    file_extension = ""
    if "." in seed_file_path:
        file_extension = str(seed_file_path).split(".")[-1]
    if not gen_file_path:
        gen_file_path = definitions.DIRECTORY_OUTPUT + "/input-" + str(values.ITERATION_NO)
    values.FILE_POC_GEN = gen_file_path
    if file_extension:
        values.FILE_POC_GEN = values.FILE_POC_GEN + "." + file_extension
    with open(values.FILE_POC_GEN, "wb") as new_input_file:
        new_input_file.write(bytearray(byte_list))


def generate_formula(formula_str):
    parser = SmtLibParser()
    script = parser.get_script(cStringIO(formula_str))
    formula = script.get_last_formula()
    return formula


def generate_ktest(argument_list, second_var_list, print_output=False):
    """
    This function will generate the ktest file provided the argument list and second order variable list
        argument_list : a list containing each argument in the order that should be fed to the program
        second_var_list: a list of tuples where a tuple is (var identifier, var size, var value)
    """
    global File_Ktest_Path
    emitter.normal("\tgenerating ktest file")
    ktest_path = File_Ktest_Path
    ktest_command = "gen-bout --out-file {0}".format(ktest_path)

    for argument in argument_list:
        index = list(argument_list).index(argument)
        if "$POC" in argument:
            binary_file_path = values.FILE_POC_GEN
            # if "_" in argument:
            #     file_index = "_".join(str(argument).split("_")[1:])
            #     binary_file_path = values.LIST_TEST_FILES[file_index]
            # else:
            #     binary_file_path = values.CONF_PATH_POC
            #     if values.FILE_POC_GEN:
            #         binary_file_path = values.FILE_POC_GEN
            #     elif values.FILE_POC_SEED:
            #         binary_file_path = values.FILE_POC_SEED
            ktest_command += " --sym-file " + binary_file_path
        elif str(index) in values.CONF_MASK_ARG:
            continue
        else:
            if argument in ["''"]:
                argument = ""
            if "\"" in argument:
                ktest_command += " --sym-arg '" + str(argument) + "'"
                continue
            ktest_command += " --sym-arg \"" + str(argument) + "\""

    for var in second_var_list:
        ktest_command += " --second-var \'{0}\' {1} {2}".format(var['identifier'], var['size'], var['value'])
    return_code = utilities.execute_command(ktest_command)
    return ktest_path, return_code


def generate_path_for_negation():
    constraint_list = []
    parser = SmtLibParser()
    emitter.normal("\tgenerating path for negation of patch constraint")
    for control_loc, sym_path in values.LIST_PPC:
        if control_loc == values.CONF_LOC_PATCH:
            script = parser.get_script(cStringIO(sym_path))
            formula = script.get_last_formula()
            patch_constraint = formula
            if formula.is_and():
                patch_constraint = formula.arg(1)
            constraint_list.append(patch_constraint.serialize())
    if not constraint_list:
        return None
    last_sym_path = values.LAST_PPC_FORMULA
    # script = parser.get_script(cStringIO(last_sym_path))
    # formula = script.get_last_formula()
    negated_path = None
    while constraint_list:
        constraint = last_sym_path
        if last_sym_path.is_and():
            constraint = last_sym_path.arg(1)
        constraint_str = constraint.serialize()
        if constraint_str in constraint_list:
            constraint_list.remove(constraint_str)
            constraint = Not(constraint)
        if negated_path is None:
            negated_path = constraint
        else:
            negated_path = And(negated_path, constraint)
        if last_sym_path.is_and():
            last_sym_path = last_sym_path.arg(0)
        else:
            break
    negated_path = And(negated_path, last_sym_path)
    return negated_path


def generate_negated_path(path_condition):
    negated_path = None
    while path_condition.is_and():
        constraint = path_condition.arg(1)
        constraint_str = constraint.serialize()
        if "angelic!" in constraint_str:
            constraint = Not(constraint)
        if negated_path is None:
            negated_path = constraint
        else:
            negated_path = And(negated_path, constraint)
        path_condition = path_condition.arg(0)
    negated_path = And(negated_path, path_condition)
    return negated_path


def generate_true_path(path_condition):
    true_path = None
    while path_condition.is_and():
        constraint = path_condition.arg(1)
        constraint_str = str(constraint.serialize())
        if "angelic!bool" in constraint_str:
            constraint = generate_true_constraint(constraint)
        elif true_path is None:
            return
        if true_path is None:
            true_path = constraint
        else:
            true_path = And(true_path, constraint)
        path_condition = path_condition.arg(0)
    true_path = And(true_path, path_condition)
    if is_unsat(true_path):
        true_path = None
    return true_path


def generate_false_path(path_condition):
    false_path = None
    while path_condition.is_and():
        constraint = path_condition.arg(1)
        constraint_str = str(constraint.serialize())
        if "angelic!bool" in constraint_str:
            constraint = generate_false_constraint(constraint)
        elif false_path is None:
            return
        if false_path is None:
            false_path = constraint
        else:
            false_path = And(false_path, constraint)
        path_condition = path_condition.arg(0)
    false_path = And(false_path, path_condition)
    if is_unsat(false_path):
        false_path = None
    return false_path


def generate_input_space(path_condition):
    partition = dict()
    var_list = generate_model(path_condition)
    for var in var_list:
        if "rvalue!" in str(var):
            constraint_info = dict()
            constraint_info['lower-bound'] = values.DEFAULT_INPUT_LOWER_BOUND
            constraint_info['upper-bound'] = values.DEFAULT_INPUT_UPPER_BOUND
            partition[str(var)] = constraint_info
    return partition


def generate_partition_for_patch_space(partition_model, patch_space, is_multi_dimension):
    partition_list = list()
    parameter_name = list(sorted(partition_model.keys()))[0]
    partition_value = partition_model[parameter_name]
    constraint_info = patch_space[parameter_name]
    constraint_info['name'] = parameter_name
    constraint_info['partition-value'] = partition_value
    param_partition_list = generate_partition_for_parameter(constraint_info, partition_value,
                                                            is_multi_dimension, parameter_name)
    del partition_model[parameter_name]
    if partition_model:
        partition_list_tmp = generate_partition_for_patch_space(partition_model, patch_space, is_multi_dimension)
        for partition_a in partition_list_tmp:
            for partition_b in param_partition_list:
                partition_b.update(partition_a)
                partition_list.append(copy.deepcopy(partition_b))
    else:
        if param_partition_list:
            partition_list = param_partition_list

    return partition_list


def generate_partition_for_input_space(partition_model, input_space, is_multi_dimension):
    partition_list = list()
    var_name = list(sorted(partition_model.keys()))[0]
    partition_value = partition_model[var_name]
    constraint_info = input_space[var_name]
    constraint_info['name'] = var_name
    constraint_info['partition-value'] = partition_value
    var_partition_list = generate_partition_for_input(constraint_info, partition_value,
                                                      is_multi_dimension, var_name)
    del partition_model[var_name]
    if partition_model:
        partition_list_tmp = generate_partition_for_input_space(partition_model, input_space, is_multi_dimension)
        for partition_a in partition_list_tmp:
            for partition_b in var_partition_list:
                partition_b.update(partition_a)
                partition_list.append(copy.deepcopy(partition_b))
    else:
        if var_partition_list:
            partition_list = var_partition_list

    return partition_list


def generate_partition_for_parameter(constraint_info, partition_value, is_multi_dimension, parameter_name):
    partition_list = list()
    partition_info = dict()
    range_info = dict()
    if is_multi_dimension:
        range_equal = (partition_value, partition_value)
        range_info['lower-bound'] = range_equal[0]
        range_info['upper-bound'] = range_equal[1]
        partition_info[parameter_name] = range_info
        partition_list.append(copy.deepcopy(partition_info))

    if constraint_info['lower-bound'] == constraint_info['upper-bound']:
        return partition_list
    range_lower = (constraint_info['lower-bound'], partition_value - 1)
    range_upper = (partition_value + 1, constraint_info['upper-bound'])

    if oracle.is_valid_range(range_lower):
        range_info['lower-bound'] = range_lower[0]
        range_info['upper-bound'] = range_lower[1]
        partition_info[parameter_name] = range_info
        partition_list.append(copy.deepcopy(partition_info))
    if oracle.is_valid_range(range_upper):
        range_info['lower-bound'] = range_upper[0]
        range_info['upper-bound'] = range_upper[1]
        partition_info[parameter_name] = range_info
        partition_list.append(copy.deepcopy(partition_info))

    return partition_list


def generate_partition_for_input(constraint_info, partition_value, is_multi_dimension, var_name):
    partition_list = list()
    partition_info = dict()
    range_info = dict()
    if is_multi_dimension:
        range_equal = (partition_value, partition_value)
        range_info['lower-bound'] = range_equal[0]
        range_info['upper-bound'] = range_equal[1]
        partition_info[var_name] = range_info
        partition_list.append(copy.deepcopy(partition_info))

    if constraint_info['lower-bound'] == constraint_info['upper-bound']:
        return partition_list
    range_lower = (constraint_info['lower-bound'], partition_value - 1)
    range_upper = (partition_value + 1, constraint_info['upper-bound'])

    if oracle.is_valid_range(range_lower):
        range_info['lower-bound'] = range_lower[0]
        range_info['upper-bound'] = range_lower[1]
        partition_info[var_name] = range_info
        partition_list.append(copy.deepcopy(partition_info))
    if oracle.is_valid_range(range_upper):
        range_info['lower-bound'] = range_upper[0]
        range_info['upper-bound'] = range_upper[1]
        partition_info[var_name] = range_info
        partition_list.append(copy.deepcopy(partition_info))

    return partition_list






# def generate_constraint_for_fixed_point(fixed_point_list):
#     formula = None
#     for var_name in fixed_point_list:
#         fixed_point = fixed_point_list[var_name]
#         sym_array = Symbol(var_name, ArrayType(BV32, BV8))
#         sym_var = BVConcat(Select(sym_array, BV(3, 32)),
#                  BVConcat(Select(sym_array, BV(2, 32)),
#                  BVConcat(Select(sym_array, BV(1, 32)), Select(sym_array, BV(0, 32)))))
#         sub_formula = Equals(sym_var, SBV(int(fixed_point), 32))
#         if formula is None:
#             formula = sub_formula
#         else:
#             formula = And(formula, sub_formula)
#     return formula
#
# def generate_new_range(constant_space, partition_list):
#     new_range_list = list()
#     constant_count = len(constant_space)
#     if constant_count == 1:
#         constant_name = list(constant_space.keys())[0]
#         constant_info = constant_space[constant_name]
#         is_continuous = constant_info['is_continuous']
#         partition_value = partition_list[constant_name]
#         if is_continuous:
#             range_lower = (constant_info['lower-bound'], partition_value - 1)
#             range_upper = (partition_value + 1, constant_info['upper-bound'])
#             if is_valid_range(range_lower):
#                 new_range_list.append((range_lower,))
#             if is_valid_range(range_upper):
#                 new_range_list.append((range_upper,))
#         else:
#             invalid_list = constant_info['invalid-list']
#             valid_list = constant_info['valid-list']
#             valid_list.remove(partition_value)
#             invalid_list.append(partition_value)
#             if valid_list:
#                 new_range_list.append((invalid_list,))
#
#     elif constant_count == 2:
#         constant_name_a = list(constant_space.keys())[0]
#         constant_name_b = list(constant_space.keys())[1]
#         constant_info_a = constant_space[constant_name_a]
#         is_continuous_a = constant_info_a['is_continuous']
#         partition_value_a = partition_list[constant_name_a]
#         constant_info_b = constant_space[constant_name_b]
#         partition_value_b = partition_list[constant_name_b]
#         is_continuous_b = constant_info_b['is_continuous']
#         if is_continuous_a:
#             range_lower_a = (constant_info_a['lower-bound'], partition_value_a - 1)
#             range_upper_a = (partition_value_a + 1, constant_info_a['upper-bound'])
#             if is_continuous_b:
#                 range_lower_b = (constant_info_b['lower-bound'], partition_value_b - 1)
#                 range_upper_b = (partition_value_b + 1, constant_info_b['upper-bound'])
#                 if is_valid_range(range_lower_a):
#                     if is_valid_range(range_lower_b):
#                         new_range_list.append((range_lower_a, range_lower_b))
#                     if is_valid_range(range_upper_b):
#                         new_range_list.append((range_lower_a, range_upper_b))
#
#                 if is_valid_range(range_upper_a):
#                     if is_valid_range(range_lower_b):
#                         new_range_list.append((range_upper_a, range_lower_b))
#                     if is_valid_range(range_upper_b):
#                         new_range_list.append((range_upper_a, range_upper_b))
#             else:
#                 invalid_list_b = constant_info_b['invalid-list']
#                 valid_list_b = constant_info_b['valid-list']
#                 valid_list_b.remove(partition_value_b)
#                 invalid_list_b.append(partition_value_b)
#                 if valid_list_b:
#                     if is_valid_range(range_lower_a):
#                         new_range_list.append((range_lower_a, invalid_list_b))
#                     if is_valid_range(range_upper_a):
#                         new_range_list.append((range_upper_a, invalid_list_b))
#
#         else:
#             if is_continuous_b:
#                 invalid_list_a = constant_info_a['invalid-list']
#                 valid_list_a = constant_info_a['valid-list']
#                 valid_list_a.remove(partition_value_a)
#                 invalid_list_a.append(partition_value_a)
#                 range_lower_b = (constant_info_b['lower-bound'], partition_value_b - 1)
#                 range_upper_b = (partition_value_b + 1, constant_info_b['upper-bound'])
#                 if valid_list_a:
#                     if is_valid_range(range_lower_b):
#                         new_range_list.append((invalid_list_a, range_lower_b))
#                     if is_valid_range(range_upper_b):
#                         new_range_list.append((invalid_list_a, range_upper_b))
#
#
#
#
#     elif constant_count == 3:
#         for constant_name_a in constant_space:
#             constant_info_a = constant_space[constant_name_a]
#             partition_value_a = partition_list[constant_name_a]
#             for constant_name_b in constant_space:
#                 constant_info_b = constant_space[constant_name_b]
#                 partition_value_b = partition_list[constant_name_b]
#                 for constant_name_c in constant_space:
#                     constant_info_c = constant_space[constant_name_c]
#                     partition_value_c = partition_list[constant_name_c]
#                     range_lower_a = (constant_info_a['lower-bound'], partition_value_a - 1)
#                     range_upper_a = (partition_value_a + 1, constant_info_a['upper-bound'])
#                     range_lower_b = (constant_info_b['lower-bound'], partition_value_b - 1)
#                     range_upper_b = (partition_value_b + 1, constant_info_b['upper-bound'])
#                     range_lower_c = (constant_info_c['lower-bound'], partition_value_c - 1)
#                     range_upper_c = (partition_value_c + 1, constant_info_c['upper-bound'])
#
#                     new_range_list.append((range_lower_a, range_lower_b, range_lower_c))
#                     new_range_list.append((range_lower_a, range_lower_b, range_upper_c))
#                     new_range_list.append((range_lower_a, range_upper_b, range_lower_c))
#                     new_range_list.append((range_lower_a, range_upper_b, range_upper_c))
#                     new_range_list.append((range_upper_a, range_lower_b, range_lower_c))
#                     new_range_list.append((range_upper_a, range_lower_b, range_upper_c))
#                     new_range_list.append((range_upper_a, range_upper_b, range_lower_c))
#                     new_range_list.append((range_upper_a, range_upper_b, range_upper_c))
#     else:
#         utilities.error_exit("unhandled constant count in generate new range")
#
#     return new_range_list


# def generate_formula_for_range(patch, constant_space, path_condition, assertion):
#     patch_constraint = extractor.extract_constraints_from_patch(patch)
#     constant_constraint = generator.generate_constant_constraint_formula(constant_space)
#     patch_constraint = And(patch_constraint, constant_constraint)
#     path_feasibility = And(path_condition, patch_constraint)
#     formula = And(path_feasibility, assertion)
#     return formula

def generate_assertion(assertion_temp, klee_dir):
    emitter.normal("\tgenerating extended specification")
    largest_path_condition = None
    max_obs = 0
    file_list = [f for f in os.listdir(klee_dir) if os.path.isfile(os.path.join(klee_dir, f))]
    for file_name in file_list:
        if ".smt2" in file_name:
            file_path = os.path.join(klee_dir, file_name)
            path_condition = extractor.extract_formula_from_file(file_path)
            model = generate_model(path_condition)
            var_list = list(model.keys())
            count_obs = 0
            declaration_line = assertion_temp[0]
            specification_line = assertion_temp[1]
            for var in var_list:
                if "obs!" in var:
                    count_obs = count_obs + 1
            if count_obs == 0:
                continue
            if max_obs < count_obs:
                max_obs = count_obs
                largest_path_condition = path_condition
    declaration_line = assertion_temp[0]
    specification_line = assertion_temp[1]
    assertion_text = ""
    for index in range(0, max_obs):
        assertion_text = assertion_text + declaration_line.replace("obs!0", "obs!" + str(index))
        assertion_text = assertion_text + specification_line.replace("obs!0", "obs!" + str(index))
    specification_formula = generate_formula(assertion_text)
    return specification_formula, max_obs


def generate_extended_patch_formula(patch_formula, path_condition):
    angelic_count = int(len(re.findall("angelic!(.+?)!0", str(path_condition.serialize()))) / 4)
    # if angelic_count == 0:
    #     print("COUNT", angelic_count)
    #     print("PATH", str(path_condition.serialize()))
    #     utilities.error_exit("angelic count is zero in extending")
    if angelic_count <= 1:
        return patch_formula
    # model_path = generate_model(path_condition)
    # var_list = list(model_path.keys())
    # count = 0
    # for var in var_list:
    #     if "angelic!bool" in var:
    #         count = count + 1
    input_list = list()

    path_script = "/tmp/z3_script_patch"
    write_smtlib(patch_formula, path_script)
    with open(path_script, "r") as script_file:
        script_lines = script_file.readlines()
    script = "".join(script_lines)
    var_list = set(re.findall("\(declare-fun (.+?) \(\)", script))
    for var in var_list:
        if "const" not in var:
            input_list.append(var)

    formula_txt = script
    formula_list = []
    for index in range(1, angelic_count):
        postfix = "_" + str(index)
        substituted_formula_txt = formula_txt
        for input_var in input_list:
            if "|" in input_var:
                input_var_postfix = input_var[:-1] + postfix + "|"
            else:
                input_var_postfix = input_var + postfix
            substituted_formula_txt = substituted_formula_txt.replace(input_var, input_var_postfix)
        formula = generate_formula(substituted_formula_txt)
        formula_list.append(formula)

    constraint_formula = patch_formula
    for formula in formula_list:
        constraint_formula = And(constraint_formula, formula)
    return constraint_formula


def generate_program_specification():
    output_dir_path = definitions.DIRECTORY_OUTPUT
    dir_list = [f for f in os.listdir(output_dir_path) if not os.path.isfile(os.path.join(output_dir_path, f))]
    expected_output_list = values.LIST_TEST_OUTPUT
    test_count = len(expected_output_list)
    max_skip_index = (test_count * 2) - 1
    program_specification = None
    for dir_name in dir_list:
        if "klee-out-repair-" not in dir_name:
            continue
        dir_path = os.path.join(output_dir_path, dir_name)
        klee_index = int(dir_name.split("-")[-1])
        # if klee_index <= max_skip_index:
        #     continue
        file_list = [f for f in os.listdir(dir_path) if os.path.isfile(os.path.join(dir_path, f))]
        for file_name in file_list:
            if ".smt2" in file_name:
                file_path = os.path.join(dir_path, file_name)
                path_condition = extractor.extract_formula_from_file(file_path)
                model_path = generate_model(path_condition)
                var_list = list(model_path.keys())
                count = 0
                for var in var_list:
                    if "obs!" in var:
                        count = count + 1
                if count == 0:
                    continue
                if program_specification is None:
                    program_specification = path_condition
                else:
                    program_specification = Or(program_specification, path_condition)

    return program_specification


def generate_ppc_from_formula(path_condition):
    ppc_list = list()
    emitter.normal("\textracting branches from path condition")
    max_count = 2 * values.DEFAULT_MAX_FLIPPINGS
    while path_condition.is_and():
        constraint = path_condition.arg(1)
        constraint_str = str(constraint.serialize())
        if "!rvalue!" in constraint_str or "!obs!" in constraint_str:
            path_condition = path_condition.arg(0)
            continue
        path_script = "/tmp/z3_script_ppc"
        write_smtlib(path_condition, path_script)
        with open(path_script, "r") as script_file:
            script_lines = script_file.readlines()
        script = "".join(script_lines)
        ppc_list.append(("-no-info-", script))
        path_condition = path_condition.arg(0)
        if len(ppc_list) > max_count:
            emitter.warning("\t[warning] maximum cap reach for branching")
            break
    return ppc_list





def generate_z3_code_for_expr(var_expr, var_name, bit_size):
    select_list = [x.group() for x in re.finditer(r'select (.*?)\)', var_expr)]
    var_name = var_name + "_" + str(bit_size)
    if bit_size == 64:
        zero = "x0000000000000000"
    else:
        zero = "x00000000"
    code = "(set-logic QF_AUFBV )\n"
    unique_source_list = []
    for sel_expr in select_list:
        symbolic_source = sel_expr.split(" ")[2]
        if symbolic_source in unique_source_list:
            continue
        unique_source_list.append(symbolic_source)
        code += "(declare-fun {} () (Array (_ BitVec 32) (_ BitVec 8) ) )\n".format(symbolic_source)
    code += "(declare-fun " + var_name + "() (_ BitVec " + str(bit_size) + "))\n"
    # code += "(declare-fun b () (_ BitVec " + str(bit_size) + "))\n"
    code += "(assert (= " + var_name + " " + var_expr + "))\n"
    # code += "(assert (not (= b #" + zero + ")))\n"
    code += "(assert  (not (= " + var_name + " #" + zero + ")))\n"
    code += "(check-sat)"
    return code


def generate_z3_code_for_var(var_expr, var_name):
    var_name = str(var_name).replace("->", "")
    var_name = str(var_name).replace("[", "-")
    var_name = str(var_name).replace("]", "-")
    if "sizeof " in var_name:
        var_name = "_".join(var_name.split(" "))
    try:
        code = generate_z3_code_for_expr(var_expr, var_name, 32)
        parser = SmtLibParser()
        script = parser.get_script(cStringIO(code))
        formula = script.get_last_formula()
        result = is_sat(formula, solver_name="z3")
    except Exception as exception:
        code = generate_z3_code_for_expr(var_expr, var_name, 64)
    return code


def generate_source_definitions(sym_expr_a, sym_expr_b):
    source_list_a = extractor.extract_input_bytes_used(sym_expr_a)
    source_list_b = extractor.extract_input_bytes_used(sym_expr_b)
    source_def_str = ""
    source_list = list(set(source_list_a + source_list_b))
    unique_source_list = []
    for source in source_list:
        source_name, _ = source.split("_")
        if source_name in unique_source_list:
            continue
        unique_source_list.append(source_name)
        source_def_str = source_def_str + \
                         ("(declare-fun {} () (Array (_ BitVec 32) (_ BitVec 8) ) )\n".
                               format(source_name))
    return source_def_str


def generate_definitions(sym_expr_a, sym_expr_b):
    lines_a = sym_expr_a.split("\n")
    var_dec_a = [x for x in lines_a if "declare" in x][-1]
    sym_expr_a = [x for x in lines_a if "assert" in x][0]
    lines_b = sym_expr_b.split("\n")
    var_dec_b = [x for x in lines_b if "declare" in x][-1]
    sym_expr_b = [x for x in lines_b if "assert" in x][0]
    var_name_a = str(var_dec_a.split(" ")[1]).replace("()", "")
    var_name_b = str(var_dec_b.split(" ")[1]).replace("()", "")

    if "_64" in var_name_a:
        if "_32" in var_name_b:
            sym_expr_b = sym_expr_b.replace(var_name_b, var_name_b + "_64_b")
            var_dec_b = var_dec_b.replace(var_name_b, var_name_b + "_64_b")
            var_name_b = var_name_b + "_64_b"
            var_dec_b = var_dec_b.replace("_ BitVec 32", "_ BitVec 64")
            var_expr_b_tokens = sym_expr_b.split(" ")
            var_expr_b_tokens[3] = "((_ zero_extend 32)" + var_expr_b_tokens[3]
            sym_expr_b = " ".join(var_expr_b_tokens)
            sym_expr_b += ")"

    if "_64" in var_name_b:
        if "_32" in var_name_a:
            sym_expr_a = sym_expr_a.replace(var_name_a, var_name_a + "_64_a")
            var_dec_a = var_dec_a.replace(var_name_a, var_name_a + "_64_a")
            var_name_a = var_name_a + "_64_a"
            var_dec_a = var_dec_a.replace("_ BitVec 32", "_ BitVec 64")
            var_expr_a_tokens = sym_expr_a.split(" ")
            var_expr_a_tokens[3] = "((_ zero_extend 32)" + var_expr_a_tokens[3]
            sym_expr_a = " ".join(var_expr_a_tokens)
            sym_expr_a += ")"

    if var_name_a == var_name_b:
        sym_expr_b = sym_expr_b.replace(var_name_b, var_name_b + "_b")
        var_dec_b = var_dec_b.replace(var_name_b, var_name_b + "_b")
        var_name_b = var_name_b + "_b"
        sym_expr_a = sym_expr_a.replace(var_name_a, var_name_a + "_a")
        var_dec_a = var_dec_a.replace(var_name_a, var_name_a + "_a")
        var_name_a = var_name_a + "_a"
    return (var_name_a, sym_expr_a, var_dec_a), (var_name_b, sym_expr_b, var_dec_b)


def generate_z3_code_for_equivalence(sym_expr_code_a, sym_expr_code_b):
    def_a, def_b = generate_definitions(sym_expr_code_a, sym_expr_code_b)
    var_name_a, sym_expr_a, var_dec_a = def_a
    var_name_b, sym_expr_b, var_dec_b = def_b
    code = "(set-logic QF_AUFBV )\n"
    code += generate_source_definitions(sym_expr_code_a, sym_expr_code_b)
    code += var_dec_a + "\n"
    code += var_dec_b + "\n"
    code += sym_expr_a + "\n"
    code += sym_expr_b + "\n"
    code += "(assert (= " + var_name_a + " " + var_name_b + "))\n"
    code += "(check-sat)"
    return code


def generate_z3_code_for_offset(sym_expr_code_a, sym_expr_code_b):
    def_a, def_b = generate_definitions(sym_expr_code_a, sym_expr_code_b)
    var_name_a, sym_expr_a, var_dec_a = def_a
    var_name_b, sym_expr_b, var_dec_b = def_b

    code = "(set-logic QF_AUFBV )\n"
    code += generate_source_definitions(sym_expr_code_a, sym_expr_code_b)
    code += var_dec_a + "\n"
    code += var_dec_b + "\n"
    if "_64" in var_name_b or "_64" in var_name_a:
        code += "(declare-fun constant_offset() (_ BitVec 64))\n"
    else:
        code += "(declare-fun constant_offset() (_ BitVec 32))\n"
    code += sym_expr_a + "\n"
    code += sym_expr_b + "\n"
    code += "(assert (= " + var_name_a + " (bvadd " + var_name_b + " constant_offset)))\n"
    code += "(check-sat)"
    return code

def generate_offset_to_line(src_file_path):
    offset_to_line = dict()
    line = 1
    with open(src_file_path, "r") as src_file:
        contents = src_file.read()
        offset = 0
        for offset, char in enumerate(contents):
            # note that we map each line to the newline character that begins the line
            # this allows us to simply add a one-indexed column number to find an offset
            # FIXME does this need to be more robust?
            if char == "\n":
                line += 1
            offset_to_line[offset] = line
        offset_to_line[offset+1] = line
    return offset_to_line


def generate_taint_sources(taint_expr_list, taint_memory_list, taint_loc):
    taint_source_list = set()
    for taint_value in taint_expr_list:
        _, taint_expr = taint_value.split(":")
        if taint_expr in taint_memory_list:
            taint_source = taint_expr
        else:
            taint_expr_code = generate_z3_code_for_var(taint_expr, "TAINT")
            taint_source = extractor.extract_input_bytes_used(taint_expr_code)
        if taint_source:
            taint_source_list.update(taint_source)
    return taint_loc, taint_source_list
