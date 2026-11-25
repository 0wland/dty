from icalendar import Calendar, Event, Alarm
from datetime import datetime, date, timedelta
import yaml

with open('data.yaml', 'r') as f:
    data = yaml.safe_load(f)

cal = Calendar()
cal.add('prodid', '-//DTY Calendar//SK//1.0')
cal.add('version', '2.0')
cal.add('X-WR-CALNAME', data['calendar']['name'])

for event_data in data['events']:
    start = date.fromisoformat(event_data['start_date'])
    end = date.fromisoformat(event_data['end_date'])

    event = Event()
    event.add('dtstart', start)
    event.add('dtend', end + timedelta(days=1))
    event.add('TRANSP', 'TRANSPARENT')
    event.add('summary', event_data['summary'])
    event.add('description', f"{event_data['summary']} dty")
    event.add('uid', f"dty-{event_data['summary'].lower().replace(' ', '-')}-{start.isoformat()}")

    reminder_date = start - timedelta(days=1)
    reminder = Event()
    reminder.add('dtstart', datetime(reminder_date.year, reminder_date.month, reminder_date.day, 12, 0))
    reminder.add('dtend', datetime(reminder_date.year, reminder_date.month, reminder_date.day, 12, 30))
    reminder.add('summary', f"🔔 REMINDER: {event_data['summary']}")
    reminder.add('description', f"{event_data['summary']} dty - začína zajtra!")
    reminder.add('TRANSP', 'TRANSPARENT')
    reminder.add('uid', f"dty-reminder-{event_data['summary'].lower()}-{reminder_date.isoformat()}")

    alarm = Alarm()
    alarm.add('action', 'DISPLAY')
    alarm.add('description', f"{event_data['summary']} dty - začína zajtra!")
    alarm.add('trigger', timedelta(0))
    reminder.add_component(alarm)

    today_reminder = Event()
    today_reminder.add('dtstart', datetime(start.year, start.month, start.day, 10, 0))
    today_reminder.add('dtend', datetime(start.year, start.month, start.day, 10, 30))
    today_reminder.add('summary', f"🔔 REMINDER: {event_data['summary']}")
    today_reminder.add('description', f"{event_data['summary']} dty - začína DNES!")
    today_reminder.add('TRANSP', 'TRANSPARENT')
    today_reminder.add('uid', f"dty-reminder-today-{event_data['summary'].lower()}-{start.isoformat()}")

    today_alarm = Alarm()
    today_alarm.add('action', 'DISPLAY')
    today_alarm.add('description', f"{event_data['summary']} dty - začína DNES!")
    today_alarm.add('trigger', timedelta(0))
    today_reminder.add_component(today_alarm)

    cal.add_component(reminder)
    cal.add_component(today_reminder)
    cal.add_component(event)

with open('calendar.ics', 'wb') as f:
    f.write(cal.to_ical())
