#!/usr/bin/env python3

from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities

desired_cap = {
 'browserName': 'iPhone',
 'device': 'iPhone 8 Plus',
 'realMobile': 'true',
 'os_version': '11',
 'name': 'Test bit.houmus.org on iPhone 8 Plus',
 'build': 'ci-30491049824',
 'project': 'bits.houmus.org'
}

driver = webdriver.Remote(
    command_executor='http://danshechter1:wsPbMqMqHVazvqMNG7Yt@hub.browserstack.com:80/wd/hub',
    desired_capabilities=desired_cap)

driver.get("http://www.google.com/ncr")
if not "Google" in driver.title:
    raise Exception("Unable to load google page!")
elem = driver.find_element_by_name("q")
elem.send_keys("BrowserStack")
elem.submit()
print(driver.title)
driver.quit()
