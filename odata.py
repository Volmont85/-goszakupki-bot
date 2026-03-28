# app/routers/odata.py
from fastapi import APIRouter, Request, Response
import httpx
import xml.etree.ElementTree as ET

router = APIRouter()

TARGET_URL = "https://goszakupki-bot-production.up.railway.app/api/results"
API_KEY = "Jf3qKrL7vT9xBz8sWp2n"

@router.get("/proxy/$metadata")
async def metadata():
    xml = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="1.0"
 xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx">
 <edmx:DataServices>
  <Schema Namespace="Default"
    xmlns="http://schemas.microsoft.com/ado/2008/09/edm">
    <EntityType Name="Message">
      <Key><PropertyRef Name="id"/></Key>
      <Property Name="id" Type="Edm.String"/>
      <Property Name="status" Type="Edm.String"/>
      <Property Name="message" Type="Edm.String"/>
    </EntityType>
    <EntityContainer Name="Container" m:IsDefaultEntityContainer="true"
      xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
      <EntitySet Name="Messages" EntityType="Default.Message"/>
    </EntityContainer>
  </Schema>
 </edmx:DataServices>
</edmx:Edmx>"""
    return Response(xml, media_type="application/xml")

@router.post("/proxy/Messages")
async def proxy_messages(request: Request):
    xml_body = await request.body()
    ns = {
        'd': 'http://schemas.microsoft.com/ado/2007/08/dataservices',
        'm': 'http://schemas.microsoft.com/ado/2007/08/dataservices/metadata'
    }

    tree = ET.fromstring(xml_body)
    def val(name):
        el = tree.find(f'.//d:{name}', ns)
        return el.text if el is not None else ""

    payload = {"id": val("id"), "status": val("status"), "message": val("message")}

    async with httpx.AsyncClient() as client:
        res = await client.post(
            TARGET_URL,
            json=payload,
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        )

    return Response(status_code=201 if res.status_code < 300 else 500)
