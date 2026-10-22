import pandas as pd
import streamlit as st

data = [ # list of 5 dicts
    {
        "name": "Bob",
        "age": 13, 
        "grades": { # each with nested "grades" dict
            "Math": 100,
            "English": 90,
            "Science": 80
        }
    },
    {
        "name": "Alice",
        "age": 12,
        "grades": {
            "Math": 100,
            "English": 90,
            "Science": 90
        }
    },
    {
        "name": "Chris",
        "age": 13,
        "grades": {
            "Math": 90,
            "English": 88,
            "Science": 76
        }
    },
    {
        "name": "Ranjit",
        "age": 12,
        "grades": {
            "Math": 67,
            "English": 59,
            "Science": 80
        }
    },
    {
        "name": "Bella",
        "age": 13,
        "grades": {
            "Math": 65,
            "English": 87,
            "Science": 67
        }
    }
]

def get_indicator(avg):
    if avg > 80:
        return "🟢"
    elif avg > 70:
        return "🟡"
    elif avg > 60:
        return "🔴"
    else:
        return ""

# build dataframe first, and explode nested grades dict
df = pd.DataFrame(data)
grades_df = pd.json_normalize(df["grades"]) # a series of dicts; pd.json_normalize flattens nested structures into tabular form
df = pd.concat([df.drop(columns=["grades"]), grades_df], axis=1) # drop grades column from df and then concatenate with the new df

# now subject columns exist at top level
df["avg"] = df[["Math", "English", "Science"]].mean(axis=1) # create and add new column that holds mean of the values of the subject columns
df["status"] = df["avg"].apply(get_indicator) # add status column containing emoji picked by get_indicator function

overall_data = df[
    ["name", "age", "avg"]
]

# build indicator column from average
overall_data["status"] = overall_data["avg"].apply(get_indicator)

selected_row = st.dataframe(
    overall_data,
    on_select="rerun", # enables row selection behaviour
    selection_mode="single-row"
)

try:
    row_id = selected_row["selection"]["rows"][0] # extract row id of the selected row
except IndexError:
    st.write("Select a row above to see more detail")
    st.stop()
    
# row_id is a positional index into overall_data; look up matching row in df
row = df.iloc[row_id]

st.write(f"Grades for {row['name']}:")
st.dataframe(
    row[["Math", "English", "Science"]].rename("Score").to_frame(),
    hide_index=False
)