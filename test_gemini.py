import google.generativeai as genai
import os

def run_test():
    """
    A simple, standalone script to test the Gemini API connection and key.
    """
    print("--- Gemini API Connection Test ---")
    
    # 1. Get the API Key from the user
    api_key = input("🔑 Please enter your Gemini API Key: ").strip()
    
    if not api_key:
        print("\n🔴 ERROR: No API key provided. Exiting.")
        return

    try:
        # 2. Configure the library with the key
        print("\n⚙️  Configuring the API with your key...")
        genai.configure(api_key=api_key)
        
        # 3. Initialize the model
        # UPDATED: Changed to a more stable and widely available model name
        model_name = 'gemini-2.5-flash'
        print(f"✨ Initializing model: '{model_name}'...")
        model = genai.GenerativeModel(model_name)
        
        # 4. Get a test prompt from the user
        print("-" * 30)
        prompt = input("📝 Enter a simple test prompt (e.g., 'What is the capital of India?'): ")
        print("-" * 30)

        if not prompt:
            print("\n🔴 ERROR: No prompt provided. Exiting.")
            return

        # 5. Call the API and get the response
        print("\n⏳ Calling the Gemini API... (This might take a moment)")
        response = model.generate_content(prompt)
        
        # 6. Print the result
        print("\n✅ SUCCESS! Received a response from the API:")
        print("-" * 30)
        print(response.text)
        print("-" * 30)

    except Exception as e:
        # 7. If anything goes wrong, print the detailed error
        print("\n🔴 FAILED: The API call ran into an error.")
        print("   This is the detailed error message from the API:")
        print(f"   --------------------")
        print(f"   {e}")
        print(f"   --------------------")
        print("\n   Please check that your API key is correct and has a project enabled for it.")

if __name__ == '__main__':
    run_test()

