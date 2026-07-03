import os, requests

res = requests.post(
    "https://sandbox.nomba.com/v1/auth/token/issue",
    headers={
        "Content-Type": "application/json",
        "accountId": "f666ef9b-888e-4799-85ce-acb505b28023",
    },
    json={
        "grant_type": "client_credentials",
        "client_id": "706df6c4-b8bb-4130-88c4-d21b052f8631",
        "client_secret": "k8UobYk3APgOoxUnNL7VpuxzwTsH4LsXtydfjcHs8RH0YISBB4OMqJsaafG+U8fWETu9YZ96bNXE+DelCDuMPw==",
    },
)
print("access_token:", res.json()["data"]["access_token"])