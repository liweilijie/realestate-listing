from adspower.sync_api.selenium import Profile, Group

group = 'scrapy'
user_id = 'kxrnlka'
my_group = Group.query(name=group)[0]
profile = Profile.query(id_=user_id)[0]
browser = profile.get_browser(ip_tab=False, headless=False, disable_password_filling=True)
browser.get('https://github.com/blnkoff/adspower')
profile.quit()