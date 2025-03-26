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

import re
from datetime import datetime

def extract_clinical_note(text):
    """Extracts structured data from the clinical note using regex with fallback values."""
    
    # Extract basic details with fallback values
    clinical_note = {
        "Patient Name": re.search(r'"Patient Name": "(.*?)"', text).group(1) if re.search(r'"Patient Name": "(.*?)"', text) else "UNKNOWN PATIENT",
        "DOB": re.search(r'"DOB": "(.*?)"', text).group(1) if re.search(r'"DOB": "(.*?)"', text) else "0000-00-00",
        "Date of Service": re.search(r'"Date of Service": "(.*?)"', text).group(1) if re.search(r'"Date of Service": "(.*?)"', text) else datetime.now().strftime("%Y-%m-%d"),
        "Provider": re.search(r'"Provider": "(.*?)"', text).group(1) if re.search(r'"Provider": "(.*?)"', text) else "UNASSIGNED PROVIDER",
        "Chief Complaint (CC)": re.search(r'"Chief Complaint \(CC\)": "(.*?)"', text).group(1) if re.search(r'"Chief Complaint \(CC\)": "(.*?)"', text) else "NO CHIEF COMPLAINT DOCUMENTED",
        "History of Present Illness (HPI)": re.search(r'"History of Present Illness \(HPI\)": \(\n?(.*?)\n?\)', text, re.DOTALL).group(1).strip() if re.search(r'"History of Present Illness \(HPI\)": \(\n?(.*?)\n?\)', text, re.DOTALL) else "NO HISTORY OF PRESENT ILLNESS DOCUMENTED"
    }
    
    # Extract Review of Systems (ROS) with fallback
    ros_match = re.search(r'"Review of Systems \(ROS\)": {\n(.*?)\n\s*}', text, re.DOTALL)
    ros_text = ros_match.group(1).strip() if ros_match else ""
    ros_dict = {}
    if ros_text:
        ros_items = re.findall(r'"(.*?)": "(.*?)"', ros_text)
        ros_dict = {key: value for key, value in ros_items}
    clinical_note["Review of Systems (ROS)"] = ros_dict if ros_dict else {"Status": "NO ROS DOCUMENTED"}

    # Extract Vital Signs with fallback
    vital_signs_match = re.search(r'"Vital Signs": {\n(.*?)\n\s*}', text, re.DOTALL)
    vital_signs_text = vital_signs_match.group(1).strip() if vital_signs_match else ""
    vital_signs_dict = {}
    if vital_signs_text:
        vital_signs_items = re.findall(r'"(.*?)": "(.*?)"', vital_signs_text)
        vital_signs_dict = {key: value for key, value in vital_signs_items}
    clinical_note["Vital Signs"] = vital_signs_dict if vital_signs_dict else {
        "Blood Pressure": "Not Measured",
        "Heart Rate": "Not Measured", 
        "Respiratory Rate": "Not Measured",
        "Temperature": "Not Measured",
        "Oxygen Saturation": "Not Measured"
    }

    # Extract Physical Examination dynamically with fallback
    physical_exam_match = re.search(r'"Physical Examination": {\n(.*?)\n\s*}', text, re.DOTALL)
    physical_exam_text = physical_exam_match.group(1).strip() if physical_exam_match else ""
    physical_exam_dict = {}
    if physical_exam_text:
        # Flat sections
        sections = re.findall(r'"(.*?)": "(.*?)"', physical_exam_text)
        physical_exam_dict = {key: value for key, value in sections}
        
        # Nested sections
        nested_sections = re.findall(r'"(.*?)": {\n(.*?)\n\s*}', physical_exam_text, re.DOTALL)
        for section, content in nested_sections:
            nested_dict = {key: value for key, value in re.findall(r'"(.*?)": "(.*?)"', content)}
            physical_exam_dict[section] = nested_dict
    
    clinical_note["Physical Examination"] = physical_exam_dict if physical_exam_dict else {"Overall": "No Physical Examination Documented"}

    # Extract Results (Prior to Imaging) with fallback
    results_match = re.search(r'"Results \(Prior to Imaging\)": \(\n?(.*?)\n?\)', text, re.DOTALL)
    clinical_note["Results (Prior to Imaging)"] = results_match.group(1).strip() if results_match else "NO RESULTS DOCUMENTED"

    # Extract Orders with fallback
    orders_match = re.search(r'"Orders": \[\n(.*?)\n\s*\]', text, re.DOTALL)
    orders_list = orders_match.group(1).strip().split('\",\n        \"') if orders_match else []
    clinical_note["Orders"] = [order.strip('"') for order in orders_list] if orders_list else ["NO ORDERS PLACED"]

    # Extract Assessment and Plan with fallback
    assessment_match = re.search(r'"Assessment": \(\n?(.*?)\n?\)', text, re.DOTALL)
    plan_match = re.search(r'"Plan": \[\n(.*?)\n\s*\]', text, re.DOTALL)

    clinical_note["Assessment and Plan"] = {
        "Assessment": assessment_match.group(1).strip() if assessment_match else "NO ASSESSMENT DOCUMENTED",
        "Plan": [p.strip('"') for p in plan_match.group(1).strip().split('\",\n        \"')] if plan_match else ["NO TREATMENT PLAN DOCUMENTED"]
    }

    return clinical_note

def extract_claims(text):
    claims = []
    claim_blocks = re.split(r"\n\d+\. Claim Number: ", text)[1:]
    
    for block in claim_blocks:
        claim = {
            "Claim Number": re.search(r"CLM\d+", block).group() if re.search(r"CLM\d+", block) else "UNKNOWN_CLAIM",
            "Patient Name": re.search(r"Patient Name: (.+)", block).group(1) if re.search(r"Patient Name: (.+)", block) else "UNKNOWN_PATIENT",
            "Date of Service": re.search(r"Date of Service: (.+)", block).group(1) if re.search(r"Date of Service: (.+)", block) else "0000-00-00",
            "Procedure Code": re.search(r"Procedure Code: (\d+)", block).group(1) if re.search(r"Procedure Code: (\d+)", block) else "00000",
            "Billed Amount": float(re.search(r"Billed Amount: \$(\d+\.\d+)", block).group(1)) if re.search(r"Billed Amount: \$(\d+\.\d+)", block) else 0.00,
            "Allowed Amount": float(re.search(r"Allowed Amount: \$(\d+\.\d+)", block).group(1)) if re.search(r"Allowed Amount: \$(\d+\.\d+)", block) else 0.00,
            "Patient Responsibility": float(re.search(r"Patient Responsibility: \$(\d+\.\d+)", block).group(1)) if re.search(r"Patient Responsibility: \$(\d+\.\d+)", block) else 0.00,
            "Paid Amount": float(re.search(r"Paid Amount: \$(\d+\.\d+)", block).group(1)) if re.search(r"Paid Amount: \$(\d+\.\d+)", block) else 0.00,
        }
        
        # Fallback for CARC Code and Description
        carc_match = re.search(r"CARC: (CO-\d+)", block)
        if carc_match:
            claim["CARC Code"] = carc_match.group(1)
            claim["CARC Description"] = carc_rarc_mapping.get(claim["CARC Code"], "Unknown CARC Description")
        else:
            claim["CARC Code"] = "N/A"
            claim["CARC Description"] = "No CARC Code Found"
        
        claims.append(claim)
    
    return claims


def validate_appeal_letter(appeal_letter, patient_name, claim_number, clinical_note):
    """
    Validate the generated appeal letter for key details and sentiment.
    
    Args:
        appeal_letter (str): Generated appeal letter
        patient_name (str): Expected patient name
        claim_number (str): Expected claim number
        clinical_note (dict): Clinical note details
    
    Returns:
        dict: Validation results
    """
    validation_results = {
        "patient_name_match": False,
        "claim_number_match": False,
        "sentiment_score": 0,
        "contains_medical_necessity": False,
    }
    
    # Check patient name
    if patient_name.lower() in appeal_letter.lower():
        validation_results["patient_name_match"] = True
    
    # Check claim number
    if claim_number.lower() in appeal_letter.lower():
        validation_results["claim_number_match"] = True
    
    # Check sentiment and tone
    try:
        sentiment_response = model.generate_content(f"""
        Analyze the sentiment and professionalism of this appeal letter:
        
        {appeal_letter}
        
        Rate the appeal's sentiment on a scale of -10 to 10, where:
        - Negative values indicate an aggressive or confrontational tone
        - 0 indicates a neutral tone
        - Positive values indicate a professional, persuasive tone
        
        Provide only the numeric sentiment score.
        """)
        
        # Extract the numeric sentiment score
        sentiment_match = re.search(r'-?\d+', sentiment_response.text)
        if sentiment_match:
            validation_results["sentiment_score"] = int(sentiment_match.group())
            print(f"Sentiment score: {validation_results['sentiment_score']}")
    except Exception as e:
        print(f"Sentiment analysis error: {e}")
    
    # Check for medical necessity language
    medical_necessity_keywords = [
        "medical necessity", "medically necessary", "clinical justification", 
        "required treatment", "essential procedure", "crucial intervention"
    ]
    
    validation_results["contains_medical_necessity"] = any(
        keyword in appeal_letter.lower() for keyword in medical_necessity_keywords
    )
    
    
    return validation_results

def generate_appeal_with_iterative_validation(claim, clinical_note, max_attempts=3):
    """
    Generate an appeal letter with iterative validation and prompt refinement.
    
    Args:
        claim (dict): Claim details
        clinical_note (dict): Clinical note details
        max_attempts (int): Maximum number of regeneration attempts
    
    Returns:
        tuple: (appeal_letter, validation_results)
    """
    base_prompt = f"""
    Patient: {clinical_note["Patient Name"]}
    DOB: {clinical_note["DOB"]}
    Date of Service: {clinical_note["Date of Service"]}
    Provider: {clinical_note["Provider"]}
    Claim Number: {claim["Claim Number"]}
    
    Claim Denial Reason: {claim.get("CARC Description", "Unknown")}
    
    Clinical Summary: {clinical_note["History of Present Illness (HPI)"]}
    
    Supporting Evidence:
    - Physical Exam Findings: {clinical_note["Physical Examination"]}
    - Orders: {clinical_note["Orders"]}
    - Assessment and Plan: {clinical_note["Assessment and Plan"]}
    - Vital Signs: {clinical_note["Vital Signs"]}
    - Review of Systems: {clinical_note["Review of Systems (ROS)"]}
    """

    iteration_prompt = base_prompt
        
    # Add initial attempt guidelines
    iteration_prompt += f"Guidelines:\n"
        
    # Add specific guidance based on previous validation attempts
    guidance_prompts = "Generate a professional, compelling appeal letter arguing the medical necessity of the procedure.Ensure the appeal letter explicitly mentions the patient's name and claim number. Use a more persuasive and professional tone that clearly demonstrates medical necessity.Critically analyze the clinical evidence and craft a highly detailed appeal.Include specific medical justifications, quote clinical findings, and use technical medical language. "
    
    iteration_prompt += guidance_prompts
    
    for attempt in range(max_attempts):
        iteration_prompt += f"\n\nAttempt {attempt + 1}:\n"
        print("Attempt:", attempt + 1)
        # Dynamically add feedback from previous validations
        additional_notes = []
        
    
        # Generate appeal with iterative guidance
        response = model.generate_content(iteration_prompt)
        appeal_letter = response.text
        
        # Validate the generated letter
        validation_results = validate_appeal_letter(
            appeal_letter, 
            clinical_note["Patient Name"], 
            claim["Claim Number"], 
            clinical_note
        )
        if not validation_results["patient_name_match"]:
            additional_notes.append(
                "Note: Previous attempt did not clearly include patient name. "
                "Explicitly state patient name throughout the letter."
            )
        
        if not validation_results["claim_number_match"]:
            additional_notes.append(
                "Note: Previous attempt did not reference claim number. "
                "Include claim number prominently in the appeal."
            )
        
        if validation_results["sentiment_score"] <= 7:
            additional_notes.append(
                "Note: Tone was not sufficiently professional. "
                "Use a more persuasive, empathetic, and clinical tone. "
                "Sound authoritative and compassionate."
            )
        
        if not validation_results["contains_medical_necessity"]:
            additional_notes.append(
                "Note: Strengthen arguments for medical necessity. "
                "Provide clear, evidence-based reasoning. "
                "Cite specific clinical findings that justify the procedure."
            )
        
        # Add additional notes if any
        if additional_notes:
            iteration_prompt += "\n\nImprovement Notes:\n" + "\n".join(additional_notes)

    return appeal_letter, validation_results
    
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

            # Generate appeals for all matching claims
            appeals = []
            validation_details_list = []
            
            for claim in claims_data:
                if claim["Patient Name"] == clinical_note["Patient Name"]:
                    appeal_letter, validation_details = generate_appeal_with_iterative_validation(claim, clinical_note)
                    appeals.append(appeal_letter)
                    validation_details_list.append(validation_details)
            
            # If no matching claims found
            if not appeals:
                appeals = ["No claim found matching the patient name."]
                validation_details_list = [{}]
            
            return render_template("index.html", 
                                   appeals=appeals, 
                                   validations=validation_details_list)

    return render_template("index.html", appeals=None, validations=None)

if __name__ == "__main__":
    app.run(debug=True)
