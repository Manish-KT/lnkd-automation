"""
Configuration and Selectors for LinkedIn Automation.
"""

# Base Configuration
LINKEDIN_BASE_URL = "https://www.linkedin.com"

# XPath Selectors
class Selectors:
    ALL_FILTERS_BUTTON = "//span[normalize-space()='All filters']/parent::button"
    
    LOCATION_SECTION = "//button[.//span[normalize-space()='Add a location']]"
    LOCATION_INPUT = "//input[@placeholder='Add a location']"
    LOCATION_OPTION = "//*[@role='option'][contains(., '{value}')]"
    
    INDUSTRY_SECTION = "//button[.//span[normalize-space()='Add an industry']]"
    INDUSTRY_INPUT = "//input[@placeholder='Add an industry']"
    INDUSTRY_OPTION = "//*[@role='option'][contains(., '{value}')]"
    
    COMPANY_SIZE_SECTION = "//*[contains(.,'Company size')]"
    COMPANY_SIZE_OPTION = "//p[contains(normalize-space(), 'employees')]"
    
    APPLY_BUTTON = "//span[normalize-space()='Show results']/parent::a"