"""Migration layer: load the grievance complaint store from either a raw
``mysqldump`` (cold start) or a live MySQL server (incremental sync). Both paths
converge on a single validated insert routine."""
