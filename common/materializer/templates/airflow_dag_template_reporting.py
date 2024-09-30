#NEW CUSTOM TEMPLATE TO HANDLE DAG DEPENDENCIES - ADDED BY GOOGLE TEAM - CUSTOM CHANGES

# Copyright 2022 Google LLC

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     https://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Disable pylance warnings
# type: ignore
# Disable all pylint warning
# pylint: skip-file

from __future__ import print_function
from airflow.operators.dummy_operator import DummyOperator
from datetime import timedelta, datetime
import airflow
from airflow.contrib.operators.bigquery_operator import BigQueryOperator
from airflow.version import version as AIRFLOW_VERSION
from airflow.sensors.external_task_sensor import ExternalTaskSensor
import json 
from croniter import croniter
import pytz

#read json data
def read_json_file(filename):
    try:
        with open(filename, 'r') as file:
            data = json.load(file)
        return data
    except FileNotFoundError:
        print(f"Error: The file '{filename}' was not found.")
        return None
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON format in '{filename}': {e}")
        return None

# Read file having the dependencies maintained in the data variable
filename = '/home/airflow/gcs/data/dag-set.json'
data = read_json_file(filename)

#generate dag full name 
def create_dag_full_name(table_name):

    #please note: financial_statement_version is populating fsv_flattened table and fsv_glaccount tables both. profit_center dag is used to populate the table profitcenter_flattened
    dag_name_exceptions = ["currency_conversion","currency_decimal","calendar_date_dim","Stock_Weekly_Snapshots_periodical_Update","Stock_Weekly_Snapshots_Initial",
    "Stock_Monthly_Snapshots_Periodical_Update","Stock_Monthly_Snapshots_Initial", "financial_statement_version", "financial_statement_periodical_load","profit_center",
    "Stock_Weekly_Snapshots_Update_Daily","Stock_Monthly_Snapshots_Daily_Update","Slow_Moving_Threshold","Stock_Characteristics_Config"]
    if table_name in dag_name_exceptions:
        return table_name
    else:
        dag_name = "_".join(
            ["${target_dataset}".replace(".", "_"), "refresh", table_name])
        dag_full_name = "_".join(
            ["${module_name}".lower(), "${target_dataset_type}", dag_name])
        return dag_full_name

#create dependency list 
try:
    if data:
        if "${table_name}" in data:
            list_dep = data["${table_name}"]
            c = 0
            for i in list_dep:
                task_id_def = "parent_task_"
                c += 1
                task_id_def = task_id_def + str(c)
                dag_full_name = create_dag_full_name(i['parent_table'])
                i.update(dag_id = dag_full_name)
                i.update(task_id = task_id_def)
except Exception as e: 
    print("Exception ", e)

def execution_delta_dependency(logical_date, **kwargs):
    dt = logical_date
    task_instance_id=str(kwargs['task_instance']).split(':')[1].split(' ')[1].split('.')[1]
    res = None
    try:
        for sub in list_dep:
            if sub['task_id'] == task_instance_id:
                res = sub
                break
        

        schedule_frequency=res['schedule_frequency']
        parent_dag_poke = ''
        print("KWARGS is ", kwargs)
        exec_dt = kwargs['context']['execution_date']
        print("Execution date is ", exec_dt)
        # base = datetime(exec_dt.year, exec_dt.month, exec_dt.day, exec_dt.hour, exec_dt.minute)
        
        print("Logical date is ", dt)
        iter = croniter(schedule_frequency, dt)
        prev_instance = iter.get_prev(datetime)
        next_instance = iter.get_next(datetime)
        #changing parent dag poke to refer to dt if current datetime = expected dag poke time 
        # refering logical date as the logical date is already T-1
        print("Previous instance is ", prev_instance)
        if next_instance == dt:
            if not dt.tzinfo:
                parent_dag_poke = pytz.utc.localize(dt)
            else:
                parent_dag_poke = dt
        else:
            #changing parent dag poke to refer to previous instance instead of again calculating a previous instance as the logical date is already T-1
            if not prev_instance.tzinfo:
                parent_dag_poke = pytz.utc.localize(prev_instance)
            else:
                parent_dag_poke = prev_instance
                
        print("Parent dag poke  ", parent_dag_poke)
        return parent_dag_poke
    
    except Exception as e1: 
        print("Exception ", e1)
        return None



default_dag_args = {
   "depends_on_past": False,
   "start_date": datetime(${year}, ${month}, ${day}),
   "catchup": False,
   "retries": 1,
   "retry_delay": timedelta(minutes=30),
}

with airflow.DAG("${dag_full_name}",
                 default_args=default_dag_args,
                 catchup=False,
                 max_active_runs=1,
                 schedule_interval="${load_frequency}") as dag:
    start_task = DummyOperator(task_id="start")
#begin of addition by Naitik on Sep 6 2024 for dag dependency utility changes
# add external task sensor for the dependent tasks    
    external_task_sensors = []
    try:
        if list_dep:
            for parent_task in list_dep:
                external_task_sensor = ExternalTaskSensor(
                    task_id=parent_task["task_id"],
                    external_dag_id=parent_task["dag_id"],
                    timeout=900,
                    execution_date_fn=execution_delta_dependency,
                    poke_interval=60,  # Check every 60 seconds
                    mode="reschedule",  # Reschedule task if external task fails
                    check_existence=True
                )
                external_task_sensors.append(external_task_sensor)
        else:
            no_ext_task = DummyOperator(task_id="no_ext_task")
            external_task_sensors.append(no_ext_task)
    except Exception as e2:
        print("Exception ", e2)
        no_ext_task = DummyOperator(task_id="no_ext_task")
        external_task_sensors.append(no_ext_task)
        
    if AIRFLOW_VERSION.startswith("1."):
        refresh_table = BigQueryOperator(
            task_id="refresh_table",
            sql="${query_file}",
            bigquery_conn_id="${lower_module_name}_${lower_tgt_dataset_type}_bq",
            use_legacy_sql=False)
    else:
        refresh_table = BigQueryOperator(
            task_id="refresh_table",
            sql="${query_file}",
            gcp_conn_id="${lower_module_name}_${lower_tgt_dataset_type}_bq",
            use_legacy_sql=False)
    stop_task = DummyOperator(task_id="stop")

# add external task sensors as the dependent tasks
    start_task >> external_task_sensors >> refresh_table >> stop_task