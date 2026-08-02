import pandas as pd

# Try different ways to read the CSV
print("Trying default read_csv...")
try:
    df = pd.read_csv("EURUSDc_M1_202601130552_202604200000.csv")
    print("Success with default read_csv")
    print("Columns:", df.columns.tolist())
    print("First row:", df.iloc[0])
except Exception as e:
    print("Failed with default read_csv:", e)

print("\nTrying with tab delimiter...")
try:
    df = pd.read_csv("EURUSDc_M1_202601130552_202604200000.csv", delimiter='\t')
    print("Success with tab delimiter")
    print("Columns:", df.columns.tolist())
    print("First row:", df.iloc[0])
except Exception as e:
    print("Failed with tab delimiter:", e)

print("\nTrying with header=0...")
try:
    df = pd.read_csv("EURUSDc_M1_202601130552_202604200000.csv", header=0)
    print("Success with header=0")
    print("Columns:", df.columns.tolist())
    print("First row:", df.iloc[0])
except Exception as e:
    print("Failed with header=0:", e)