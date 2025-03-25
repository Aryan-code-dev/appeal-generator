import os
import re
import google.generativeai as genai
from flask import Flask, request, render_template
from dotenv import load_dotenv

load_dotenv()

# Configure Gemini API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash")

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# CARC/RARC Code Mapping
carc_rarc_mapping = {
    "CO-50": "Medical Necessity",
    "CO-45": "Coding Error or Fee Schedule Issue",
    "N30": "Coverage Issue"
}

def extract_clinical_note(text):
    """Extracts structured data from the clinical note using regex."""
    
    # Extract basic details
    clinical_note = {
        "Patient Name": re.search(r'"Patient Name": "(.*?)"', text).group(1),
        "DOB": re.search(r'"DOB": "(.*?)"', text).group(1),
        "Date of Service": re.search(r'"Date of Service": "(.*?)"', text).group(1),
        "Provider": re.search(r'"Provider": "(.*?)"', text).group(1),
        "Chief Complaint (CC)": re.search(r'"Chief Complaint \(CC\)": "(.*?)"', text).group(1),
        "History of Present Illness (HPI)": re.search(r'"History of Present Illness \(HPI\)": \(\n?(.*?)\n?\)', text, re.DOTALL).group(1).strip()
    }
    
    # Extract Review of Systems (ROS)
    ros_match = re.search(r'"Review of Systems \(ROS\)": {\n(.*?)\n\s*}', text, re.DOTALL)
    ros_text = ros_match.group(1).strip() if ros_match else ""
    ros_dict = {}
    if ros_text:
        ros_items = re.findall(r'"(.*?)": "(.*?)"', ros_text)
        ros_dict = {key: value for key, value in ros_items}
    clinical_note["Review of Systems (ROS)"] = ros_dict

    # Extract Vital Signs
    vital_signs_match = re.search(r'"Vital Signs": {\n(.*?)\n\s*}', text, re.DOTALL)
    vital_signs_text = vital_signs_match.group(1).strip() if vital_signs_match else ""
    vital_signs_dict = {}
    if vital_signs_text:
        vital_signs_items = re.findall(r'"(.*?)": "(.*?)"', vital_signs_text)
        vital_signs_dict = {key: value for key, value in vital_signs_items}
    clinical_note["Vital Signs"] = vital_signs_dict

    # Extract Physical Examination dynamically
    physical_exam_match = re.search(r'"Physical Examination": {\n(.*?)\n\s*}', text, re.DOTALL)
    physical_exam_text = physical_exam_match.group(1).strip() if physical_exam_match else ""
    physical_exam_dict = {}
    if physical_exam_text:
        sections = re.findall(r'"(.*?)": "(.*?)"', physical_exam_text)
        physical_exam_dict = {key: value for key, value in sections}
        
        # Extract nested sections dynamically
        nested_sections = re.findall(r'"(.*?)": {\n(.*?)\n\s*}', physical_exam_text, re.DOTALL)
        for section, content in nested_sections:
            nested_dict = {key: value for key, value in re.findall(r'"(.*?)": "(.*?)"', content)}
            physical_exam_dict[section] = nested_dict
    
    clinical_note["Physical Examination"] = physical_exam_dict

    # Extract Results (Prior to Imaging)
    clinical_note["Results (Prior to Imaging)"] = re.search(r'"Results \(Prior to Imaging\)": \(\n?(.*?)\n?\)', text, re.DOTALL).group(1).strip()

    # Extract Orders
    orders_match = re.search(r'"Orders": \[\n(.*?)\n\s*\]', text, re.DOTALL)
    orders_list = orders_match.group(1).strip().split('\",\n        \"') if orders_match else []
    clinical_note["Orders"] = [order.strip('"') for order in orders_list]

    # Extract Assessment and Plan
    assessment_match = re.search(r'"Assessment": \(\n?(.*?)\n?\)', text, re.DOTALL)
    plan_match = re.search(r'"Plan": \[\n(.*?)\n\s*\]', text, re.DOTALL)

    clinical_note["Assessment and Plan"] = {
        "Assessment": assessment_match.group(1).strip() if assessment_match else "",
        "Plan": [p.strip('"') for p in plan_match.group(1).strip().split('\",\n        \"')] if plan_match else []
    }

    return clinical_note

def extract_claims(text):
    claims = []
    claim_blocks = re.split(r"\n\d+\. Claim Number: ", text)[1:]
    
    for block in claim_blocks:
        claim = {
            "Claim Number": re.search(r"CLM\d+", block).group(),
            "Patient Name": re.search(r"Patient Name: (.+)", block).group(1),
            "Date of Service": re.search(r"Date of Service: (.+)", block).group(1),
            "Procedure Code": re.search(r"Procedure Code: (\d+)", block).group(1),
            "Billed Amount": float(re.search(r"Billed Amount: \$(\d+\.\d+)", block).group(1)),
            "Allowed Amount": float(re.search(r"Allowed Amount: \$(\d+\.\d+)", block).group(1)),
            "Patient Responsibility": float(re.search(r"Patient Responsibility: \$(\d+\.\d+)", block).group(1)),
            "Paid Amount": float(re.search(r"Paid Amount: \$(\d+\.\d+)", block).group(1)),
        }
        carc_match = re.search(r"CARC: (CO-\d+)", block)
        if carc_match:
            claim["CARC Code"] = carc_match.group(1)
            claim["CARC Description"] = carc_rarc_mapping.get(claim["CARC Code"], "Unknown")
        claims.append(claim)
    
    return claims

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        # Save uploaded files
        era_file = request.files["era"]
        clinical_file = request.files["clinical"]
        
        if era_file and clinical_file:
            era_path = os.path.join(UPLOAD_FOLDER, "era.txt")
            clinical_path = os.path.join(UPLOAD_FOLDER, "clinical_note.txt")
            era_file.save(era_path)
            clinical_file.save(clinical_path)

            # Read file contents
            with open(era_path, "r", encoding="utf-8") as f:
                era_text = f.read()
            with open(clinical_path, "r", encoding="utf-8") as f:
                clinical_text = f.read()
            
            # Process files
            clinical_note = extract_clinical_note(clinical_text)
            claims_data = extract_claims(era_text)

            # Generate appeal
            appeal_letter = "No claim found matching the patient name."
            for claim in claims_data:
                if claim["Patient Name"] == clinical_note["Patient Name"]:
                    denial_reason = claim.get("CARC Description", "Unknown")
                    
                    appeal_prompt = f"""
                    Patient: {clinical_note["Patient Name"]}
                    DOB: {clinical_note["DOB"]}
                    Date of Service: {clinical_note["Date of Service"]}
                    Provider: {clinical_note["Provider"]}
                    Claim Number: {claim["Claim Number"]}
                    
                    Claim Denial Reason: {denial_reason}
                    
                    Clinical Summary: {clinical_note["History of Present Illness (HPI)"]}
                    
                    Supporting Evidence:
                    - Physical Exam Findings: {clinical_note["Physical Examination"]}
                    - Orders: {clinical_note["Orders"]}
                    - Assessment and Plan: {clinical_note["Assessment and Plan"]}
                    - Vital Signs: {clinical_note["Vital Signs"]}
                    - Review of Systems: {clinical_note["Review of Systems (ROS)"]}
                    
                    Based on the above clinical information, please generate an appeal letter arguing the medical necessity of the procedure.
                    """

                    response = model.generate_content(appeal_prompt)
                    appeal_letter = response.text
                    break  # Stop after finding the first matching claim
            
            return render_template("index.html", appeal=appeal_letter)

    return render_template("index.html", appeal=None)

if __name__ == "__main__":
    app.run(debug=True)
