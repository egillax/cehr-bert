import os
import argparse
import datetime

from pyspark.sql import SparkSession

import spark_apps.parameters as p
from utils.common import *

VISIT_CONCEPT_IDS = [9201, 9203, 262]

HEART_FAILURE_CONCEPTS = [45773075, 45766964, 45766167, 45766166, 45766165, 45766164, 44784442, 44784345, 44782733,
                          44782728, 44782719, 44782718, 44782713, 44782655, 44782428, 43530961, 43530643, 43530642,
                          43022068, 43022054, 43021842, 43021841, 43021840, 43021826, 43021825, 43021736, 43021735,
                          43020657, 43020421, 40486933, 40482857, 40481043, 40481042, 40480603, 40480602, 40479576,
                          40479192, 37311948, 37309625, 37110330, 36717359, 36716748, 36716182, 36713488, 36712929,
                          36712928, 36712927, 35615055, 4327205, 4311437, 4307356, 4284562, 4273632, 4267800, 4264636,
                          4259490, 4242669, 4233424, 4233224, 4229440, 4215802, 4215446, 4206009, 4205558, 4199500,
                          4195892, 4195785, 4193236, 4185565, 4177493, 4172864, 4142561, 4141124, 4139864, 4138307,
                          4124705, 4111554, 4108245, 4108244, 4103448, 4079695, 4079296, 4071869, 4030258, 4023479,
                          4014159, 4009047, 4004279, 3184320, 764877, 764876, 764874, 764873, 764872, 764871, 762003,
                          762002, 444101, 444031, 443587, 443580, 442310, 439846, 439698, 439696, 439694, 319835,
                          316994, 316139, 314378, 312927]

AGE_LOWER_BOUND = 10
AGE_UPPER_BOUND = 80
BUFFER_PERIOD_IN_DAYS = 90

DOMAIN_TABLE_LIST = ['condition_occurrence', 'drug_exposure', 'procedure_occurrence']

PERSON = 'person'
VISIT_OCCURRENCE = 'visit_occurrence'
CONDITION_OCCURRENCE = 'condition_occurrence'


def main(spark, input_folder, output_folder, date_filter):
    patient_ehr_records = extract_ehr_records(spark, input_folder, DOMAIN_TABLE_LIST)

    person = spark.read.parquet(os.path.join(input_folder, PERSON))
    visit_occurrence = spark.read.parquet(os.path.join(input_folder, VISIT_OCCURRENCE))
    condition_occurrence = spark.read.parquet(os.path.join(input_folder, CONDITION_OCCURRENCE))

    visits = visit_occurrence.where(F.col('visit_concept_id').isin(VISIT_CONCEPT_IDS))
    heart_failure_conditions = condition_occurrence.where(F.col('condition_concept_id').isin(HEART_FAILURE_CONCEPTS))

    positive_hf_cases = visits.join(heart_failure_conditions, heart_failure_conditions['visit_occurrence_id'] == visits[
        'visit_occurrence_id']).select(
        visits['visit_occurrence_id'], visits['person_id'], visits['visit_start_date']).distinct()

    positive_hf_cases = positive_hf_cases \
        .withColumn('visit_start_date', F.to_date('visit_start_date', 'yyyy-MM-dd')) \
        .where(F.col('visit_start_date') >= date_filter).withColumn('visit_order', F.dense_rank().over(
        W.partitionBy('person_id').orderBy('visit_start_date', 'visit_occurrence_id'))) \
        .where(F.col('visit_order') == 1).select('visit_occurrence_id', 'person_id', 'visit_start_date') \
        .withColumn('label', F.lit(1))

    hf_person_ids = positive_hf_cases.select(F.col('person_id').alias('positive_person_id')).distinct()

    negative_hf_cases = visits.join(hf_person_ids, F.col('person_id') == F.col('positive_person_id'), 'left') \
        .where(F.col('positive_person_id').isNull()) \
        .select(visits['visit_occurrence_id'], visits['person_id'], visits['visit_start_date']).distinct() \
        .withColumn('visit_start_date', F.to_date('visit_start_date', 'yyyy-MM-dd')) \
        .where(F.col('visit_start_date') >= date_filter) \
        .withColumn('visit_order', F.dense_rank().over(
        W.partitionBy('person_id').orderBy(F.desc('visit_start_date'), 'visit_occurrence_id'))) \
        .where(F.col('visit_order') == 1).select('visit_occurrence_id', 'person_id', 'visit_start_date') \
        .withColumn('label', F.lit(0))

    fh_cohort = positive_hf_cases.union(negative_hf_cases)

    fh_cohort = fh_cohort.join(person, 'person_id') \
        .withColumn('age', F.year('visit_start_date') - F.col('year_of_birth')) \
        .where(F.col('age').between(AGE_LOWER_BOUND, AGE_UPPER_BOUND)) \
        .select([F.col(field_name) for field_name in fh_cohort.schema.fieldNames()] + [F.col('age')])

    fh_cohort_ehr_records = patient_ehr_records.join(fh_cohort,
                                                     patient_ehr_records['person_id'] == fh_cohort['person_id']) \
        .where((patient_ehr_records['date'] <= F.date_sub(fh_cohort['visit_start_date'], BUFFER_PERIOD_IN_DAYS)) & (
            patient_ehr_records['visit_occurrence_id'] != fh_cohort['visit_occurrence_id'])) \
        .select(patient_ehr_records['person_id'], patient_ehr_records['standard_concept_id'],
                patient_ehr_records['date'], patient_ehr_records['visit_occurrence_id'], patient_ehr_records['domain'])

    sequence_data = create_sequence_data(fh_cohort_ehr_records, date_filter)

    sequence_data.join(fh_cohort.select(['person_id', 'label']), 'person_id') \
        .write.mode('overwrite').parquet(os.path.join(output_folder, p.heart_failure_data_path))


def valid_date(s):
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        msg = "Not a valid date: '{0}'.".format(s)
        raise argparse.ArgumentTypeError(msg)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Arguments for generating mortality labels')
    parser.add_argument('-i',
                        '--input_folder',
                        dest='input_folder',
                        action='store',
                        help='The path for your input_folder where the sequence data is',
                        required=True)
    parser.add_argument('-o',
                        '--output_folder',
                        dest='output_folder',
                        action='store',
                        help='The path for your output_folder',
                        required=True)
    parser.add_argument('-f',
                        '--date_filter',
                        dest='date_filter',
                        action='store',
                        help='The path for your output_folder',
                        required=True,
                        type=valid_date)

    ARGS = parser.parse_args()

    spark = SparkSession.builder.appName('Generate Mortality labels').getOrCreate()
    main(spark, ARGS.input_folder, ARGS.output_folder, ARGS.date_filter)