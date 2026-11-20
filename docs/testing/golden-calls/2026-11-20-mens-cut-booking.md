# Golden live-call transcript — successful booking

- **Date:** 20 November 2026 (NZ time)
- **Call started:** 2026-11-20 03:13:38.889704 (UTC timestamp as recorded by PRISM LINK)
- **Caller:** [REDACTED]
- **Tenant:** Luxe Hair & Beauty
- **Outcome:** `booked`
- **Scenario:** Men's Cut, Friday at 10:00 AM; receptionist correctly resolves the request to Friday 27 November 2026, checks availability, captures caller details, reads the booking back, and confirms the appointment.
- **Test path:** Live mobile call through the configured Hero Internet trunk.
- **Evidence type:** Successful live-call transcript. This is a golden reference for future regression coverage; it is not itself an automated test.

## Transcript

20/11/2026, 3:13:38 am | RECEPTIONIST: Thanks for calling Luxe Hair & Beauty! You are speaking with the salon’s AI receptionist. What can I do for you today?

20/11/2026, 3:13:50 am | CALLER: Can I book a Mens haircut, please?

20/11/2026, 3:13:50 am | RECEPTIONIST: What day and time would you like?

20/11/2026, 3:13:54 am | CALLER: Friday.

20/11/2026, 3:13:54 am | RECEPTIONIST: What time on Friday would you like?

20/11/2026, 3:13:56 am | CALLER: 10:00.

20/11/2026, 3:13:56 am | RECEPTIONIST: Great, Friday 27 November 2026 at 10:00 AM is available. May I have your name, please?

20/11/2026, 3:14:09 am | CALLER: [CALLER NAME REDACTED].

20/11/2026, 3:14:09 am | RECEPTIONIST: Thanks, [CALLER NAME]. What's your phone number?

20/11/2026, 3:14:22 am | CALLER: [PHONE NUMBER REDACTED].

20/11/2026, 3:14:22 am | RECEPTIONIST: To confirm: Men's Cut on Friday 27 November 2026 at 10:00 AM for [CALLER NAME]. Phone [PHONE NUMBER]. Is that correct?

20/11/2026, 3:14:37 am | CALLER: Yep.

20/11/2026, 3:14:39 am | RECEPTIONIST: Your Men's Cut is booked for Friday 27 November 2026 at 10:00 AM.

20/11/2026, 3:14:48 am | CALLER: Thank you.

20/11/2026, 3:14:48 am | RECEPTIONIST: You're welcome. Have a great day.

20/11/2026, 3:14:53 am | CALLER: Okay then. Bye.

## Golden acceptance criteria

1. Recognise “Mens haircut” as the configured Men's Cut service.
2. Capture the weekday and time across separate caller turns.
3. Resolve “Friday, 10:00” to Friday 27 November 2026 at 10:00 AM for this call's date/time context.
4. Confirm availability before asking for personal details.
5. Capture and accurately repeat the caller's name and phone number.
6. Obtain explicit caller confirmation before booking.
7. Confirm the appointment as booked, with the correct service, date, and time.
8. End the call naturally without changing the confirmed booking.
