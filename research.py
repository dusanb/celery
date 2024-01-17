from utils import scraper
from utils import gpt

from celery import Celery

celery_app = Celery('research', backend='redis://localhost', broker='redis://localhost//')


def research_url(sanitized_url: str, theme: None):
    # if theme is supplied, scrape the supplied URL to n depth.
    # Then, send all the markdown files into an Assistant and then ask the question
    # Expensive and a bit slow, but should cover most cases.  

    scraped_dict = {}
    prompt = f"""
    Summarize the content you are provided in layman's terms.\n
    If you encounter any errors about Wordpress, please ignore them.\n
    """
    tokens = 1000

    max_links = None

    

    if (theme is not None):
        scrape_depth=1
        max_links=10
        prompt += "Please focus your results on " + theme + ".\n"
        tokens = 4000
    else:
        scrape_depth=0
    try:
        scraper.scrape_url(sanitized_url, scraped_dict, scrape_depth, max_links)
    except scraper.UrlUnreachableException as e:
        return "Unable to summarize this URL because it was unreachable.\n " + e.message

    #check length of scraped_dict
    if len(scraped_dict) > 1:
        #we have more than one markdown file, so we need to combine them
        assistant = gpt.create_or_retrieve_summary_assistant()
        #iterate over dictionary, saving the HTML content to disk using the Scraper module
        #then add the file to the assistant
        files = []
        for key in scraped_dict:
            value = scraped_dict[key]
            markdown = value["markdown"]
            #hash the value of value["h1"] to get a unique filename
            filename = "/tmp/" + scraper.generate_unique_filename(value["h1"])
            scraper.write_markdown_to_file(filename, markdown)
            files.append(filename)
        if (len(files) > 9):
            #we can only add 20 files at a time so delete the extras
            files = files[0:9]

        messages = gpt.run_assistant(assistant, prompt, files)
        return messages.data[0].content[0].text.value

    else:
        #we only have one markdown file, so we can just use that
        first_key = next(iter(scraped_dict))
        first_value = scraped_dict[first_key]
        markdown =  first_value["markdown"]

        summary = gpt.invoke_chat_gpt(prompt = prompt, 
            reference_text = markdown, max_tokens = tokens)
        
        return summary.content
    
@celery_app.task
def research_url_thematically(sanitized_url: str, theme: None):
    #in this method we are going to generate a list of candidates links
    #by scraping the root URL, passing to chatGPT and asking for no more than
    #3 links which have the best chance of containing the thematic information.

    all_links = []
    try:
        all_links = scraper.scrape_links(sanitized_url)
    except scraper.UrlUnreachableException as e:
        return "Unable to summarize this URL because it was unreachable.\n" + e.message

    if (len(all_links) < 4):
        #we don't have enough links to bother asking for the top 3
        return research_url(sanitized_url, theme)
    
    #convert the list of links to a string with line breaks
    all_links_str = "\n".join(all_links)

    prompt = f"""
    Please list at most 3 URLs that seem relevant to {theme}. Separate them with line breaks. Do not return any other 
    text except the URLs. \n
    If none of the links are relevant, return an empty string.\n
    These are the candidate links:\n
    {all_links_str}\n
    """

    #fix this hacky solution to max_tokens
    relevant_links_message = gpt.invoke_chat_gpt(prompt, 
        "", model = "gpt-4", max_tokens= 3500)
    
    if (relevant_links_message.content == ""):
        print("!!! couldn't find any relevant links, so just summarizing the whole thing")
        return research_url(sanitized_url, theme)

    #convert the string of links to a list
    relevant_links = relevant_links_message.content.split("\n")

    assistant = gpt.create_or_retrieve_summary_assistant()    

    files = []
    for link in relevant_links:
        scraped_dict = {}
        scraper.scrape_url(link, scraped_dict, 0)

        for key in scraped_dict:
            value = scraped_dict[key]
            markdown = value["markdown"]
            #hash the value of value["h1"] to get a unique filename
            filename = "/tmp/" + scraper.generate_unique_filename(value["h1"])
            scraper.write_markdown_to_file(filename, markdown)
            files.append(filename)
    
    prompt = f"""
    Summarize the content you are provided in layman's terms
    focusing on {theme}.\n
    If you encounter any errors about Wordpress, please ignore them.\n
    """

    messages = gpt.run_assistant(assistant, prompt, files)

    summary = None

    #check that messages is not None and that we have more than one message
    if messages is not None and len(messages.data) > 1:
        #so we got back more that just our prompt
        summary = messages.data[0].content[0].text.value
        
    return summary