"""Compile actual notification handoffs with a typed transport fake; no device/network use.

Run normally for green; --baseline drops the language transport field to
reproduce English notifications with a configured Danish interface.
"""
from pathlib import Path
import argparse
import subprocess
import tempfile
ROOT = Path(__file__).resolve().parents[4]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--baseline", action="store_true")
args = parser.parse_args()
source=(ROOT/'src/ios/DaythreadApp/CompletionNotifications.swift').read_text()
def method(name):
    marker='func '+name+'('
    start=source.index(marker)
    body=source.index('{',start)
    depth=1; end=body+1
    while depth:
        if source[end]=='{':depth+=1
        elif source[end]=='}':depth-=1
        end+=1
    return source[start:end]
code=r'''
import Foundation
struct UIBackgroundTaskIdentifier: Equatable { let value:Int; static let invalid = Self(value:-1) }
@MainActor final class UIApplication {
 static let shared = UIApplication()
 func registerForRemoteNotifications() {}
 func beginBackgroundTask(withName:String) -> UIBackgroundTaskIdentifier { .init(value:1) }
 func endBackgroundTask(_ id:UIBackgroundTaskIdentifier) {}
}
@MainActor enum L10n {
 static let languages = ["en", "da", "de", "es", "fr", "nb", "sv"]
 static var preference="da"
 static var deviceLanguages=["en-US"]
 static var language:String { resolveLanguage(preference:preference, preferredLanguages:deviceLanguages) }
 static func error(_ error:Error)->String { "failure" }
 __RESOLVE__
}
struct CompletionNotification:Decodable { let id:UUID; let kind:String; let target:Target; struct Target:Decodable {let jobID:UUID?} }
struct Inbox { var cursor=0; mutating func receive(_ event:CompletionNotification)->Bool {false}; mutating func advanceCursor(to:Int){cursor=to} }
enum JSONValue:Equatable {case bool(Bool),null;subscript(_ key:String)->Self{.bool(true)} }
@MainActor final class Client {
 struct Call {let path:String;let method:String;let body:[String:Any];let query:[String:String]}
 var calls:[Call]=[]
 var feeds=0
 var delayForegroundRegistration=false
 func request(_ path:String,method:String="GET",body:[String:Any]?=nil) async throws -> JSONValue {
  calls.append(.init(path:path,method:method,body:body ?? [:],query:[:]))
  if delayForegroundRegistration && method=="PUT" && body?["active"] as? Bool == true {try await Task.sleep(for:.seconds(30))}
  return .bool(true)
 }
 func decoded<T:Decodable>(_ path:String,query:[String:String],as:T.Type) async throws -> T {
  calls.append(.init(path:path,method:"GET",body:[:],query:query));feeds+=1
  return try JSONDecoder().decode(T.self,from:Data(#"{"notifications":[],"cursor":0}"#.utf8))
 }
}
typealias BackendClient = Client
@MainActor final class Harness {
 var active=false
 var generation=UUID()
 var operation:Task<Void,Never>?
 var client:Client?=Client()
 var authorized=true
 var token:String?="same-token"
 var registeredToken:String?
 var registeredLanguage:String?
 var presentationLanguage="da"
 var installation="installation"
 var pendingPush:([AnyHashable:Any],Bool)?
 var pushConfigured=false
 var status:String?
 var previewRevision=UUID()
 var rehydrate=true
 var inbox=Inbox()
 var banner:CompletionNotification?
 var clearCount=0
 static let pushEnvironment="sandbox"
 func refreshPermission() async {}
 func receivePush(_ payload:[AnyHashable:Any],open:Bool){}
 func clearClickAcknowledgement(jobID:UUID){}
 func clearPreviews(){clearCount+=1;rehydrate=true;previewRevision=UUID()}
 func save(){}
 private struct Page:Decodable {let notifications:[CompletionNotification];let cursor:Int}
__METHODS__
}
@main struct Main {
 @MainActor static func waitForFeed(_ client:Client,_ count:Int) async {for _ in 0..<10000 {if client.feeds>=count {return}; await Task.yield()}; preconditionFailure("Feed request did not start")}
 @MainActor static func main() async {
  let service=Harness();let client=service.client!
  service.active=true
  let task=Task {await service.foregroundLoop(generation:service.generation)}
  await waitForFeed(client,1);task.cancel();await task.value
  precondition(client.calls.first{$0.method=="PUT"}?.body["interface_language"] as? String == "da","RED: configured Danish missing from registration")
  precondition(client.calls.first{$0.path=="v1/notifications"}?.query["interface_language"] == "da","Configured language missing from feed")
  L10n.preference="en"
  service.setActive(true)
  await waitForFeed(client,2);service.operation?.cancel();await service.operation?.value
  precondition(service.clearCount == 1 && client.calls.last{$0.path=="v1/notifications"}?.query["after"] == "0", "Language change must rehydrate old previews")
  precondition(client.calls.filter{$0.method=="PUT"}.count==2,"Same token must register again after language changes")
  precondition(client.calls.last{$0.method=="PUT"}?.body["interface_language"] as? String == "en")
  precondition(client.calls.last{$0.path=="v1/notifications"}?.query["interface_language"] == "en")
  let race=Harness();let transport=race.client!
  race.registeredToken="same-token";race.registeredLanguage="da";race.presentationLanguage="da"
  transport.delayForegroundRegistration=true
  race.setActive(true)
  while transport.calls.isEmpty {await Task.yield()}
  race.setActive(false)
  await race.operation?.value
  let last=transport.calls.last{$0.method=="PUT"}
  precondition(last?.body["interface_language"] as? String == "en" && last?.body["active"] as? Bool == false,"Language update must survive immediate backgrounding")
  print("PASS: configured DA registration/feed, same-token EN update, DA→EN→background handoff")
 }
}
'''
methods='\n'.join(method(n) for n in ['setActive','foregroundLoop','readFeed'])
if args.baseline:
    methods=methods.replace(', "interface_language": language', '')
code=code.replace('__METHODS__',methods)
localization=(ROOT/'src/ios/Sources/OmiCore/AppLocalization.swift').read_text()
start=localization.index('public static func resolveLanguage(')
end=localization.index('    public static var locale', start)
code=code.replace('__RESOLVE__',localization[start:end])
with tempfile.TemporaryDirectory(prefix="daythread-notification-language-") as directory:
    directory=Path(directory)
    swift=directory/'Check.swift'
    binary=directory/'check'
    swift.write_text(code)
    subprocess.run(['swiftc','-parse-as-library',str(swift),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True,timeout=10)
