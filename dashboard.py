
import requests
import streamlit as st

st.set_page_config(layout="wide")
st.title("Resume Intelligence Dashboard")
backend_url = st.text_input("Backend URL", "http://localhost:8000/resumeParse")
uploaded = st.file_uploader("Upload OCRed JSON resume", type=["json"])
if uploaded and st.button("Analyze"):
    files = {"file": (uploaded.name, uploaded.getvalue(), "application/json")}
    with st.spinner("Analyzing..."):
        response = requests.post(backend_url, files=files, timeout=900)
    if response.ok:
        data = response.json()
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Total Score", data["scorecard"]["total_score"])
        c2.metric("Band", data["scorecard"]["band"])
        c3.metric("Top Role Family", data["semantic_analysis"]["top_role_family"])
        c4.metric("DNA", data["dna_fit"]["primary_dna"])
        st.subheader("Recruiter Summary"); st.write(data.get("recruiter_summary"))
        st.subheader("Top Skills"); st.json(data["skill_analysis"]["top_skills"][:10])
        st.subheader("Semantic Analysis"); st.json(data["semantic_analysis"])
        st.subheader("Qualitative Analysis"); st.json(data["qualitative_analysis"])
        st.subheader("Raw Output"); st.json(data)
    else:
        st.error(response.text)
