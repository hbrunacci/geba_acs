import pyodbc

cn = pyodbc.connect(
    "DRIVER={ODBC Driver 18 for SQL Server};"
    "SERVER=192.168.0.6,1433;"
    "DATABASE=sxvs_geba;"
    "UID=sa;"
    "PWD=kvy2012*.;"
    "Encrypt=no;"
    "TrustServerCertificate=yes;"
)
print("OK")
cn.close()
