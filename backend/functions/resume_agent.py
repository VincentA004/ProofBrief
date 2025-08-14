import os
import json
import boto3
from dotenv import load_dotenv

# --- Core Logic Function ---

def generate_final_brief(
    resume_text: str,
    jd_text: str,
    artifacts: list,
    heuristics: dict
) -> dict:
    """
    Constructs a detailed prompt and calls a powerful Bedrock LLM for final analysis.
    """
    print("Constructing final prompt for the synthesis agent.")
    bedrock = boto3.client("bedrock-runtime")
    
    # --- Prompt Engineering ---
    # This is the "recipe" for our Head Chef. We provide all the prepped
    # ingredients and give very specific instructions for the final output format.
    prompt = f"""
    You are an expert technical hiring manager providing a final, data-driven analysis of a candidate.
    Based on the comprehensive data provided below, generate a concise and factual candidate brief.
    Your entire response must be a single, valid JSON object.

    ## CONTEXT ##

    # Job Description:
    {jd_text}

    # Candidate's Resume Text:
    {resume_text}

    # Candidate's Scraped Public Artifacts:
    {json.dumps(artifacts, indent=2)}

    # Objective Heuristic Scores (Keyword counts from resume and artifacts):
    {json.dumps(heuristics, indent=2)}

    ## INSTRUCTIONS ##

    Generate a JSON object with the following keys:
    - "summary": A 3-bullet point summary of the candidate's fit for the role.
    - "evidence_highlights": A list of 3-5 key pieces of evidence. Each item in the list must be an object with keys "claim", "evidence_url", and "justification".
    - "risk_flags": A list of 1-3 potential risks or areas to probe in an interview.
    - "screening_questions": A list of 4 tailored, open-ended screening questions based on comparing the candidate's evidence to the job's requirements.
    """

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "temperature": 0.1,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ]
    })
    
    try:
        # Use a powerful model for the final synthesis step
        response = bedrock.invoke_model(
            body=body,
            modelId="anthropic.claude-3-sonnet-20240229-v1:0", 
            contentType="application/json",
            accept="application/json"
        )
        response_body = json.loads(response.get("body").read())
        llm_output_text = response_body['content'][0]['text']
        print("Successfully received response from Bedrock.")
        return json.loads(llm_output_text)
    except Exception as e:
        print(f"Error calling Bedrock: {e}")
        raise

# --- AWS Lambda Handler ---

def handler(event, context):
    """
    The Lambda handler which receives the enriched data from the previous step
    and calls the core logic function.
    """
    print("ResumeAgent function triggered.")
    
    # Extract all the pre-processed data from the input event
    brief_id = event["briefId"]
    resume_text = event["resumeText"]
    jd_text = event["jdText"]
    artifacts = event["scrapedArtifacts"]
    heuristics = event["heuristicScores"]
    
    # Call the core logic function to get the final brief content
    llm_content = generate_final_brief(resume_text, jd_text, artifacts, heuristics)
    
    # Return the final content, ready for the PDF generation step
    return {
        "briefId": brief_id,
        "finalContent": llm_content
    }

# --- Local Testing Block ---

if __name__ == '__main__':
    print("--- Running Local Test for ResumeAgent ---")
    load_dotenv()
    
    # For local testing, this script expects a file named 'sample_input.json'
    # in the same directory. This file should contain the output from the
    # previous 'ProcessContent' function.
    try:
        with open('sample_input.json', 'r') as f:
            test_event = json.load(f)
        
        # Call the handler to simulate a Lambda invocation
        result = handler(test_event, None)
        
        print("\n--- Local Test Successful ---")
        print(json.dumps(result, indent=2))

    except FileNotFoundError:
        print("\nERROR: Please create a 'sample_input.json' file with the output from the previous step.")
    except Exception as e:
        print(f"\n--- Local Test Failed: {e} ---")