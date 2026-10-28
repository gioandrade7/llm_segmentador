import pandas as pd

docs = pd.read_csv("docs.csv")

print(docs.head())
print(docs.columns)
print(docs.loc[0]['TEXTONORMA'])