//
//  LocalAuth.swift
//  PetiGo Project
//
//  Created by Lazaro Yovanys Carabeo Vazquez on 2026-03-15.
//

import Foundation
import LocalAuthentication

func authenticateUser() {
    let context = LAContext()
    var error: NSError?

    // 1. Check if biometrics are available
    if context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &error) {
        let reason = "Identify yourself!"

        // 2. Perform authentication
        context.evaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, localizedReason: reason) { success, authenticationError in
            
            // 3. Update UI on the main thread
            DispatchQueue.main.async {
                if success {
                    // Authenticated successfully
                    print("Success")
                } else {
                    // Authentication failed
                    print("Error: \(authenticationError?.localizedDescription ?? "Unknown error")")
                }
            }
        }
    } else {
        // No biometrics or not configured
        print("Biometrics not available")
    }
}
