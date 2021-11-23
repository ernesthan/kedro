import gc

import pytest
from delta import DeltaTable
from psutil import Popen
from pyspark import SparkContext
from pyspark.sql import SparkSession
from pyspark.sql.types import IntegerType, StringType, StructField, StructType

from kedro.extras.datasets.spark import DeltaTableDataset, SparkDataSet
from tests.extras.datasets.spark.conftest import UseTheSparkSessionFixtureOrMock


# clean up pyspark after the test module finishes
@pytest.fixture(scope="module", autouse=True)
def delta_spark_session(replace_spark_default_getorcreate):
    SparkSession.builder.getOrCreate = replace_spark_default_getorcreate

    try:
        spark = (
            SparkSession.builder.config(
                "spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension"
            )
            .config(
                "spark.sql.catalog.spark_catalog",
                "org.apache.spark.sql.delta.catalog.DeltaCatalog",
            )
            .config("spark.jars.packages", "io.delta:delta-core_2.12:1.0.0")
            .getOrCreate()
        )
        yield spark

        # This fixture should be a dependency of other fixtures dealing with spark hive data
        # in this module so that it always exits last and stops the spark session
        # after tests are finished.
        spark.stop()
    except PermissionError:  # pragma: no cover
        # On Windows machine TemporaryDirectory can't be removed because some
        # files are still used by Java process.
        pass

    SparkSession.builder.getOrCreate = UseTheSparkSessionFixtureOrMock

    # remove the cached JVM vars
    SparkContext._jvm = None  # pylint: disable=protected-access
    SparkContext._gateway = None  # pylint: disable=protected-access

    # py4j doesn't shutdown properly so kill the actual JVM process
    for obj in gc.get_objects():
        try:
            if isinstance(obj, Popen) and "pyspark" in obj.args[0]:
                obj.terminate()  # pragma: no cover
        except ReferenceError:  # pragma: no cover
            # gc.get_objects may return dead weak proxy objects that will raise
            # ReferenceError when you isinstance them
            pass


@pytest.fixture
def sample_spark_df():
    schema = StructType(
        [
            StructField("name", StringType(), True),
            StructField("age", IntegerType(), True),
        ]
    )

    data = [("Alex", 31), ("Bob", 12), ("Clarke", 65), ("Dave", 29)]

    return SparkSession.builder.getOrCreate().createDataFrame(data, schema)


class TestDeltaTableDataSet:
    def test_load(self, tmp_path, sample_spark_df):
        filepath = (tmp_path / "test_data").as_posix()
        spark_delta_ds = SparkDataSet(filepath=filepath, file_format="delta")
        spark_delta_ds.save(sample_spark_df)
        loaded_with_spark = spark_delta_ds.load()
        assert loaded_with_spark.exceptAll(sample_spark_df).count() == 0

        delta_ds = DeltaTableDataset(filepath=filepath)
        delta_table = delta_ds.load()

        assert isinstance(delta_table, DeltaTable)
        loaded_with_deltalake = delta_table.toDF()
        assert loaded_with_deltalake.exceptAll(loaded_with_spark).count() == 0

    def test_save(self, tmp_path, sample_spark_df, caplog):
        filepath = (tmp_path / "test_data").as_posix()
        delta_ds = DeltaTableDataset(filepath=filepath)
        assert not delta_ds.exists()

        delta_ds.save(sample_spark_df)
        # save is a dummy operation, check that indeed nothing is written
        assert not delta_ds.exists()

        log_messages = [r.getMessage() for r in caplog.records]
        expected_log_message = (
            "Saving was performed on `DeltaTable` object "
            "within the context of the node function"
        )
        assert expected_log_message in log_messages

    def test_exists(self, tmp_path, sample_spark_df):
        filepath = (tmp_path / "test_data").as_posix()
        delta_ds = DeltaTableDataset(filepath=filepath)

        assert not delta_ds.exists()

        spark_delta_ds = SparkDataSet(filepath=filepath, file_format="delta")
        spark_delta_ds.save(sample_spark_df)

        assert delta_ds.exists()
