from kg_construction.db_manager import TimeSeriesManager, BlobManager, KGManager

if __name__ == "__main__":

    answer = input("Are you sure you want to clear all databases? (y/n): ")
    
    if answer == "y":

        # Clear the time series database
        time_series_manager = TimeSeriesManager("","")
        time_series_manager.clear_database()

        # Clear the blob database
        # blob_manager = BlobManager()
        # blob_manager.clear_database()

        # Clear the neo4j database
        kg_manager = KGManager()
        kg_manager.clear_database()

        # Clear the vector store
        kg_manager.vector_db.mq_client.index("incidents").delete()
